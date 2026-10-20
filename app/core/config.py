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

# Email delivery backends. ``console`` is development-only.
ALLOWED_EMAIL_BACKENDS: frozenset[str] = frozenset({"console", "smtp", "disabled"})

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
    # Used to build the links sent by email (verification / password reset).
    # It points at the *client*, not necessarily this service.
    public_base_url: str = Field(
        default="http://localhost:8000",
        description="Public base URL used when composing links sent to users.",
    )

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

    # --- Profiles ---
    # How often ``last_seen_at`` may be refreshed by an authenticated request.
    last_seen_update_interval_seconds: int = Field(default=60, ge=0)
    # Whether login records the client IP on the device session. The IP is only
    # ever shown back to the account owner; switch this off where storing it is
    # not permitted.
    session_store_ip: bool = Field(default=True)

    # --- User search ---
    search_min_query_length: int = Field(
        default=2,
        ge=1,
        le=32,
        description="Shortest accepted search term (prevents wide scans).",
    )
    search_default_limit: int = Field(default=20, ge=1, le=100)
    search_max_limit: int = Field(default=50, ge=1, le=100)

    # --- Chat ---
    # Hard ceilings for one page, regardless of what a client asks for.
    conversation_max_limit: int = Field(default=50, ge=1, le=100)
    message_max_limit: int = Field(default=100, ge=1, le=200)

    # --- Email delivery ---
    # ``console`` writes messages to EMAIL_OUTBOX_DIR (development only),
    # ``smtp`` sends over the network, ``disabled`` drops them.
    email_backend: str = Field(default="console", description="console | smtp | disabled")
    email_from: str = Field(default="no-reply@messenger.local")
    email_outbox_dir: str = Field(
        default="var/outbox",
        description="Where the console backend writes rendered messages.",
    )
    email_verification_token_expire_minutes: int = Field(default=1440, ge=1)
    password_reset_token_expire_minutes: int = Field(default=30, ge=1)

    # SMTP (only used when EMAIL_BACKEND=smtp)
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default="")
    smtp_password: SecretStr = Field(default=SecretStr(""))
    smtp_use_tls: bool = Field(default=True)

    # --- Per-scope rate limits -------------------------------------------------
    # Account-security and lookup endpoints get their own throttle so one noisy
    # feature cannot exhaust another's budget.
    email_rate_limit_attempts: int = Field(default=3, ge=1)
    email_rate_limit_window_seconds: int = Field(default=3600, ge=1)
    password_reset_rate_limit_attempts: int = Field(default=5, ge=1)
    password_reset_rate_limit_window_seconds: int = Field(default=3600, ge=1)
    search_rate_limit_attempts: int = Field(default=60, ge=1)
    search_rate_limit_window_seconds: int = Field(default=60, ge=1)
    contact_rate_limit_attempts: int = Field(default=30, ge=1)
    contact_rate_limit_window_seconds: int = Field(default=60, ge=1)
    chat_rate_limit_attempts: int = Field(default=120, ge=1)
    chat_rate_limit_window_seconds: int = Field(default=60, ge=1)

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

    @field_validator("email_backend")
    @classmethod
    def _validate_email_backend(cls, value: str, info: ValidationInfo) -> str:
        """Restrict the backend and forbid the development one in production."""
        backend = value.strip().lower()
        if backend not in ALLOWED_EMAIL_BACKENDS:
            allowed = ", ".join(sorted(ALLOWED_EMAIL_BACKENDS))
            raise ValueError(f"email_backend must be one of: {allowed}")

        environment = str(info.data.get("app_env", "development")).lower()
        if backend == "console" and environment not in _LOCAL_ENVIRONMENTS:
            raise ValueError(
                "email_backend='console' is a development-only backend and is not "
                "allowed outside local environments"
            )
        return backend

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
