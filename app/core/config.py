"""Application configuration loaded from environment variables.

Configuration values are read from, in order of precedence:

1. Real environment variables
2. A local ``.env`` file (see ``.env.example`` for the supported keys)
3. The defaults declared on :class:`Settings`

Secrets (the JWT signing key) are **only** read from the environment and are
never given a usable default, so the application refuses to start without one.

Usage::

    from app.core.config import get_settings

    settings = get_settings()
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# JWT algorithms we are willing to sign/verify with. Restricted to HMAC so it is
# impossible to configure an unsafe algorithm such as ``none`` or to trigger an
# RSA/HMAC confusion attack.
ALLOWED_JWT_ALGORITHMS: frozenset[str] = frozenset({"HS256", "HS384", "HS512"})

# Substrings that identify the throw-away placeholder shipped in `.env.example`.
# They are rejected outside local development so an insecure secret cannot reach
# a real environment by accident.
INSECURE_SECRET_MARKERS: frozenset[str] = frozenset(
    {"change-me", "changeme", "replace-me", "please-change", "example-secret"}
)

# Environments where the placeholder secret is tolerated.
_LOCAL_ENVIRONMENTS: frozenset[str] = frozenset({"development", "local", "test", "testing"})

MIN_SECRET_LENGTH = 32


class DatabaseSettings(BaseSettings):
    """The subset of settings needed to reach the database.

    Kept free of secrets so tooling such as Alembic can resolve the database URL
    without requiring the application's JWT key.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Database ---
    # A SQLAlchemy URL. SQLite is the default; swapping this value for a
    # PostgreSQL URL (e.g. ``postgresql+psycopg://user:pass@host/db``) is the
    # only change required to move to PostgreSQL later on.
    database_url: str = Field(
        default="sqlite:///./messenger.db",
        description="SQLAlchemy database URL.",
    )
    db_echo: bool = Field(default=False, description="Log every SQL statement.")

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Reject an empty URL and accept common shorthand for PostgreSQL."""
        url = value.strip()
        if not url:
            raise ValueError("database_url must not be empty")
        # ``postgres://`` is emitted by some hosting providers but SQLAlchemy
        # only understands ``postgresql://``.
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url


class Settings(DatabaseSettings):
    """Strongly typed application settings."""

    # --- Application metadata ---
    app_name: str = Field(default="Messenger API", description="Human readable service name.")
    app_env: str = Field(default="development", description="Deployment environment name.")
    app_debug: bool = Field(default=False, description="Enable debug behaviour and verbose errors.")
    app_version: str = Field(default="0.1.0", description="Semantic version of the API.")

    # --- API ---
    api_v1_prefix: str = Field(default="/api/v1", description="Prefix for version 1 endpoints.")

    # --- Server ---
    host: str = Field(default="127.0.0.1", description="Bind host for the ASGI server.")
    port: int = Field(default=8000, ge=1, le=65535, description="Bind port for the ASGI server.")

    # --- Authentication / JWT ---
    # No default on purpose: the key must be supplied through the environment.
    jwt_secret_key: SecretStr = Field(
        ...,
        description="HMAC secret used to sign access tokens (environment only).",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm (HMAC only).")
    access_token_expire_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Lifetime of an access token in minutes.",
    )
    jwt_issuer: str = Field(default="messenger-api", description="``iss`` claim of issued tokens.")
    jwt_audience: str = Field(default="messenger-api", description="``aud`` claim of issued tokens.")

    # --- Login rate limiting (in-process, see app/core/rate_limit.py) ---
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable the in-process login rate limiter.",
    )
    login_rate_limit_attempts: int = Field(
        default=5,
        ge=1,
        description="Maximum login attempts per window and key.",
    )
    login_rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        description="Length of the login rate-limit window in seconds.",
    )

    # --- CORS ---
    # ``NoDecode`` keeps pydantic-settings from JSON-decoding the value, so a
    # plain comma-separated list in `.env` works.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Origins allowed to call the API from a browser.",
    )

    @field_validator("api_v1_prefix")
    @classmethod
    def _normalise_api_prefix(cls, value: str) -> str:
        """Ensure the API prefix always starts with ``/`` and has no trailing ``/``."""
        prefix = value.strip()
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"
        return prefix.rstrip("/")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow CORS origins to be provided as a comma-separated string."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def _validate_jwt_algorithm(cls, value: str) -> str:
        """Only allow the HMAC algorithms we explicitly support."""
        algorithm = value.strip().upper()
        if algorithm not in ALLOWED_JWT_ALGORITHMS:
            allowed = ", ".join(sorted(ALLOWED_JWT_ALGORITHMS))
            raise ValueError(f"jwt_algorithm must be one of: {allowed}")
        return algorithm

    @field_validator("jwt_secret_key")
    @classmethod
    def _validate_jwt_secret(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        """Enforce a minimum entropy length and refuse example secrets in production."""
        secret = value.get_secret_value()
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"jwt_secret_key must be at least {MIN_SECRET_LENGTH} characters long"
            )

        environment = str(info.data.get("app_env", "development")).lower()
        lowered = secret.lower()
        if environment not in _LOCAL_ENVIRONMENTS and any(
            marker in lowered for marker in INSECURE_SECRET_MARKERS
        ):
            raise ValueError("jwt_secret_key looks like an example value and is not allowed here")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    The cache keeps configuration reads cheap while still allowing the
    environment to be loaded only once per process.
    """
    return Settings()


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return database settings without requiring application secrets."""
    return DatabaseSettings()
