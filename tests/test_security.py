"""Unit tests for password hashing, the password policy and JWT handling."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    PASSWORD_MAX_LENGTH,
    PasswordPolicyError,
    TokenError,
    access_token_ttl_seconds,
    create_access_token,
    decode_access_token,
    hash_password,
    validate_password_policy,
    verify_password,
)

SECRET = "unit-test-secret-0123456789abcdefghijklmnopqrstuvwxyz"
STRONG_PASSWORD = "Str0ng-Passw0rd!"
DATABASE_URL = "postgresql+psycopg://test:test@127.0.0.1:5432/test"


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url=DATABASE_URL,
        jwt_secret_key=SECRET,
        access_token_expire_minutes=15,
    )


# --- Password policy --------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "Short1!a",  # too short (9 chars)
        "alllowercase1!",  # no upper case
        "ALLUPPERCASE1!",  # no lower case
        "NoDigitsHere!!",  # no digit
        "NoSpecial1234",  # no special character
        "A" * PASSWORD_MAX_LENGTH + "a1!",  # too long
    ],
)
def test_weak_passwords_are_rejected(password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password_policy(password)


def test_strong_password_is_accepted() -> None:
    assert validate_password_policy(STRONG_PASSWORD) == STRONG_PASSWORD


# --- Argon2id ---------------------------------------------------------------


def test_hash_is_argon2id_and_not_the_raw_password() -> None:
    hashed = hash_password(STRONG_PASSWORD)

    assert hashed != STRONG_PASSWORD
    assert STRONG_PASSWORD not in hashed
    assert hashed.startswith("$argon2id$")


def test_same_password_hashes_differently() -> None:
    """A per-hash salt must make identical passwords produce different hashes."""
    assert hash_password(STRONG_PASSWORD) != hash_password(STRONG_PASSWORD)


def test_verify_password() -> None:
    hashed = hash_password(STRONG_PASSWORD)

    assert verify_password(STRONG_PASSWORD, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_with_garbage_hash_is_false() -> None:
    assert verify_password(STRONG_PASSWORD, "not-a-hash") is False


# --- JWT --------------------------------------------------------------------


def test_token_round_trip(settings: Settings) -> None:
    token, expires_at = create_access_token(1, settings=settings)
    claims = decode_access_token(token, settings=settings)

    assert claims.subject == "1"
    assert claims.token_type == "access"
    assert claims.jti
    # ``exp`` is stored as an epoch second, so compare at second resolution.
    assert int(expires_at.timestamp()) == int(claims.expires_at.timestamp())


def test_ttl_helper(settings: Settings) -> None:
    assert access_token_ttl_seconds(settings) == 15 * 60


def test_expired_token_is_rejected(settings: Settings) -> None:
    token, _ = create_access_token(1, settings=settings, expires_delta=timedelta(seconds=-1))

    with pytest.raises(TokenError):
        decode_access_token(token, settings=settings)


def test_tampered_token_is_rejected(settings: Settings) -> None:
    token, _ = create_access_token(1, settings=settings)
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")

    with pytest.raises(TokenError):
        decode_access_token(tampered, settings=settings)


def test_token_signed_with_another_secret_is_rejected(settings: Settings) -> None:
    other = Settings(
        app_env="test",
        database_url=DATABASE_URL,
        jwt_secret_key="a-completely-different-secret-value-123456",
    )
    token, _ = create_access_token(1, settings=other)

    with pytest.raises(TokenError):
        decode_access_token(token, settings=settings)


def test_token_with_wrong_audience_is_rejected(settings: Settings) -> None:
    other = Settings(
        app_env="test",
        database_url=DATABASE_URL,
        jwt_secret_key=SECRET,
        jwt_audience="someone-else",
    )
    token, _ = create_access_token(1, settings=other)

    with pytest.raises(TokenError):
        decode_access_token(token, settings=settings)


def test_alg_none_token_is_rejected(settings: Settings) -> None:
    """A token that claims ``alg: none`` must never be accepted."""
    forged = jwt.encode(
        {"sub": "1", "jti": "x", "iat": 0, "exp": 9999999999, "iss": "messenger-api",
         "aud": "messenger-api", "type": "access"},
        key="",
        algorithm="none",
    )

    with pytest.raises(TokenError):
        decode_access_token(forged, settings=settings)


def test_algorithm_allowlist_rejects_unsafe_configuration() -> None:
    with pytest.raises(ValueError):
        Settings(
            app_env="test",
            database_url=DATABASE_URL,
            jwt_secret_key=SECRET,
            jwt_algorithm="none",
        )


def test_secret_is_required_and_long_enough() -> None:
    with pytest.raises(ValueError):
        Settings(
            app_env="test",
            database_url=DATABASE_URL,
            jwt_secret_key="too-short",
        )
