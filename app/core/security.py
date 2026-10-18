"""Password hashing and JWT handling.

Passwords
---------
Hashed with **Argon2id** (``argon2-cffi``) using the RFC 9106 second
recommended profile: 64 MiB of memory, 3 iterations, 4 lanes. The raw password
is never stored, logged or returned.

Tokens
------
Short-lived **HS256** JWTs. Verification is explicit about the signing
algorithm (allowlist, never taken from the token header), the audience, the
issuer and the expiry, and every token carries a unique ``jti`` that the
application can later revoke.

The signing key is read from :class:`~app.core.config.Settings` (environment
only). Functions accept an optional ``settings`` argument so callers can pass
the settings their application was built with (important for tests); it
defaults to the process-wide cached settings.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings, get_settings

# --- Password policy (documented in README.md) ------------------------------

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
PASSWORD_SPECIAL_CHARACTERS = "!@#$%^&*(),.?\":{}|<>_+-=[]/\\;'`~"

_PASSWORD_POLICY = (
    f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters long and "
    "contain at least one lowercase letter, one uppercase letter, one digit and one "
    "special character."
)


class PasswordPolicyError(ValueError):
    """Raised when a password does not satisfy the documented policy."""


def validate_password_policy(password: str) -> str:
    """Return ``password`` if it satisfies the policy, otherwise raise.

    The policy is deliberately explicit and testable: length between
    :data:`PASSWORD_MIN_LENGTH` and :data:`PASSWORD_MAX_LENGTH` and at least one
    character from each of four classes.
    """
    if not isinstance(password, str):
        raise PasswordPolicyError(_PASSWORD_POLICY)

    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(_PASSWORD_POLICY)

    checks = (
        re.search(r"[a-z]", password),
        re.search(r"[A-Z]", password),
        re.search(r"\d", password),
        re.search(f"[{re.escape(PASSWORD_SPECIAL_CHARACTERS)}]", password),
    )
    if not all(checks):
        raise PasswordPolicyError(_PASSWORD_POLICY)
    return password


# --- Argon2id hashing -------------------------------------------------------

# One shared hasher: constructing it is not free and it is stateless/thread-safe.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A valid hash used to equalise timing when the account does not exist.

    Computed once, lazily, so importing this module stays cheap.
    """
    return _hasher.hash("dummy-password-for-constant-time-comparison")


def hash_password(password: str) -> str:
    """Hash a raw password with Argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a raw password against an Argon2id hash without raising."""
    try:
        return _hasher.verify(hashed_password, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_password_dummy(password: str) -> None:
    """Burn a comparable amount of time when no user exists.

    Prevents a timing side channel that would otherwise reveal whether an email
    is registered.
    """
    verify_password(password, _dummy_hash())


def password_needs_rehash(hashed_password: str) -> bool:
    """Return ``True`` if the hash was produced with outdated parameters."""
    try:
        return _hasher.check_needs_rehash(hashed_password)
    except InvalidHashError:
        return True


# --- JWT --------------------------------------------------------------------


class TokenError(Exception):
    """Raised when a token is malformed, expired or fails verification."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Verified claims of an access token."""

    subject: str
    jti: str
    issued_at: datetime
    expires_at: datetime
    token_type: str

    @property
    def subject_id(self) -> int | None:
        """The ``sub`` claim as an integer id, or ``None`` when not numeric."""
        try:
            return int(self.subject)
        except (TypeError, ValueError):
            return None


def _resolved(settings: Settings | None) -> Settings:
    return settings or get_settings()


def create_access_token(
    subject: str | int,
    *,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Create a signed access token.

    Returns the encoded token and its expiry (UTC, timezone-aware).
    """
    config = _resolved(settings)
    issued_at = now or datetime.now(timezone.utc)
    lifetime = expires_delta or timedelta(minutes=config.access_token_expire_minutes)
    expires_at = issued_at + lifetime

    payload = {
        "sub": str(subject),
        "jti": uuid.uuid4().hex,
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": config.jwt_issuer,
        "aud": config.jwt_audience,
        "type": "access",
    }

    token = jwt.encode(
        payload,
        config.jwt_secret_key.get_secret_value(),
        algorithm=config.jwt_algorithm,
    )
    return token, expires_at


def decode_access_token(token: str, *, settings: Settings | None = None) -> AccessTokenClaims:
    """Verify a token and return its claims.

    Raises :class:`TokenError` for anything that does not verify. The algorithm
    is taken from configuration — never from the token itself — which prevents
    algorithm-confusion attacks.
    """
    config = _resolved(settings)
    try:
        payload = jwt.decode(
            token,
            config.jwt_secret_key.get_secret_value(),
            algorithms=[config.jwt_algorithm],
            audience=config.jwt_audience,
            issuer=config.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "jti", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:  # includes ExpiredSignatureError, InvalidSignatureError
        raise TokenError("Could not validate credentials.") from exc

    if payload.get("type") != "access":
        raise TokenError("Unexpected token type.")

    try:
        return AccessTokenClaims(
            subject=str(payload["sub"]),
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=timezone.utc),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
            token_type=str(payload["type"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("Could not validate credentials.") from exc


def access_token_ttl_seconds(settings: Settings | None = None) -> int:
    """Lifetime of a newly issued access token, in seconds."""
    return _resolved(settings).access_token_expire_minutes * 60
