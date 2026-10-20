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

import hashlib
import re
import secrets
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


# --- Username and profile-field policy -------------------------------------

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
DISPLAY_NAME_MAX_LENGTH = 64
BIO_MAX_LENGTH = 280
AVATAR_KEY_MAX_LENGTH = 255

# Message bodies are plain text in this phase (no markup, no attachments). The
# cap keeps a single row bounded and is mirrored by a CHECK constraint on the
# table, so the limit also holds when application code is bypassed.
MESSAGE_MAX_LENGTH = 4000

# Usernames are stored lower-cased and restricted to a portable character set.
# Lower-casing at the boundary makes uniqueness case-insensitive identically on
# SQLite and PostgreSQL, so a plain unique index is enough. The first character
# must be alphanumeric so handles cannot look like internal markers (``_x``).
USERNAME_PATTERN = re.compile(
    rf"^[a-z0-9][a-z0-9_]{{{USERNAME_MIN_LENGTH - 1},{USERNAME_MAX_LENGTH - 1}}}$"
)

# Names that would collide with API routes or be confusing in URLs.
RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "administrator",
        "anonymous",
        "api",
        "auth",
        "block",
        "blocks",
        "contact",
        "contacts",
        "docs",
        "help",
        "login",
        "logout",
        "me",
        "moderator",
        "openapi",
        "redoc",
        "register",
        "root",
        "search",
        "security",
        "settings",
        "signup",
        "support",
        "system",
        "user",
        "users",
    }
)

_USERNAME_POLICY = (
    f"Username must be {USERNAME_MIN_LENGTH}-{USERNAME_MAX_LENGTH} characters, start with a "
    "letter or digit, and contain only lowercase letters, digits and underscores."
)

# Opaque object-storage key: ``<namespace>/<owner-id>/<uuid>.<extension>``.
# Deliberately strict so an arbitrary URL or traversal sequence cannot be stored.
AVATAR_KEY_PATTERN = re.compile(
    rf"^avatars/[1-9][0-9]*/[0-9a-f]{{32}}\.(png|jpe?g|webp|gif)$"
)


class UsernamePolicyError(ValueError):
    """Raised when a username does not satisfy the documented policy."""


def normalize_username(value: str) -> str:
    """Return the canonical form of a username (trimmed and lower-cased)."""
    return value.strip().lower()


def validate_username(value: str) -> str:
    """Return the normalised username if it is acceptable, otherwise raise."""
    if not isinstance(value, str):
        raise UsernamePolicyError(_USERNAME_POLICY)

    username = normalize_username(value)
    if not USERNAME_PATTERN.match(username):
        raise UsernamePolicyError(_USERNAME_POLICY)
    if username in RESERVED_USERNAMES:
        raise UsernamePolicyError("That username is reserved.")
    return username


def validate_avatar_key(value: str) -> str:
    """Validate a server-issued avatar object key.

    Users never submit this value directly; it is produced by the (future)
    upload service. The strict pattern keeps arbitrary URLs, path traversal and
    unsupported formats out of the database.
    """
    if not isinstance(value, str) or not AVATAR_KEY_PATTERN.match(value):
        raise ValueError("Invalid avatar key.")
    return value


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
    # Matches ``User.token_version`` at issue time. Tokens minted before this
    # claim existed carry version 0, which is the column default, so they keep
    # working until the version is deliberately bumped.
    version: int = 0

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
    version: int = 0,
    jti: str | None = None,
) -> tuple[str, datetime]:
    """Create a signed access token.

    Returns the encoded token and its expiry (UTC, timezone-aware).

    ``jti`` may be supplied so the caller can record the session id *before*
    issuing the token (login stores it on the device session row).
    """
    config = _resolved(settings)
    issued_at = now or datetime.now(timezone.utc)
    lifetime = expires_delta or timedelta(minutes=config.access_token_expire_minutes)
    expires_at = issued_at + lifetime

    payload = {
        "sub": str(subject),
        "jti": jti or uuid.uuid4().hex,
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": config.jwt_issuer,
        "aud": config.jwt_audience,
        "type": "access",
        "ver": int(version),
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
            version=int(payload.get("ver", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("Could not validate credentials.") from exc


def access_token_ttl_seconds(settings: Settings | None = None) -> int:
    """Lifetime of a newly issued access token, in seconds."""
    return _resolved(settings).access_token_expire_minutes * 60


# --- Opaque, single-use tokens (email verification, password reset) ---------

#: Number of random bytes behind every opaque token.
OPAQUE_TOKEN_BYTES = 32


def generate_opaque_token() -> str:
    """Return a fresh, high-entropy URL-safe token.

    Used for email-verification and password-reset links. The raw value is only
    ever handed to the email service; the database stores a digest.
    """
    return secrets.token_urlsafe(OPAQUE_TOKEN_BYTES)


def hash_opaque_token(token: str) -> str:
    """Return the hex SHA-256 digest stored in the database.

    A fast hash is the right tool here (unlike passwords): the token already has
    256 bits of entropy, so there is nothing to brute force, and lookups by
    digest must stay cheap.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
