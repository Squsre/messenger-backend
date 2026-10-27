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

import re
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# JWT algorithms we are willing to sign/verify with. Restricted to HMAC so it is
# impossible to configure an unsafe algorithm such as ``none`` or to trigger an
# RSA/HMAC confusion attack.
ALLOWED_JWT_ALGORITHMS: frozenset[str] = frozenset({"HS256", "HS384", "HS512"})

# Redis transports. ``rediss`` is accepted for deployments that terminate TLS at
# the Redis endpoint.
ALLOWED_REDIS_SCHEMES: frozenset[str] = frozenset({"redis", "rediss"})

# Email delivery backends. ``console`` is development-only.
ALLOWED_EMAIL_BACKENDS: frozenset[str] = frozenset({"console", "smtp", "disabled"})

# Media backends. The HTTP layer is backend-agnostic; local disk is useful for
# development/tests and MinIO is the durable object-storage backend used by the
# Compose stack. See app/services/storage.py.
ALLOWED_MEDIA_STORAGE_BACKENDS: frozenset[str] = frozenset({"local", "minio"})

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

    Deliberately independent of the application's JWT secret so tooling such as
    Alembic can resolve ``DATABASE_URL`` without loading the full application
    configuration. The URL itself may contain a database password; it is read
    from the environment or a Git-ignored ``.env`` and must never be committed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Database ---
    # PostgreSQL is the only supported database. The URL is required so the
    # application can never silently fall back to a local file database.
    database_url: str = Field(
        ...,
        description="PostgreSQL SQLAlchemy URL (postgresql+psycopg://...).",
    )
    db_echo: bool = Field(default=False, description="Log every SQL statement.")

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Require PostgreSQL and accept common URL shorthand."""
        url = value.strip()
        if not url:
            raise ValueError("database_url must not be empty")
        # ``postgres://`` is emitted by some hosting providers but SQLAlchemy
        # only understands ``postgresql://``.
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError(
                "database_url must use PostgreSQL with the psycopg driver "
                "(postgresql+psycopg://...)"
            )
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

    # --- Redis (cache, shared rate limiting and ephemeral data) ---
    # PostgreSQL remains the source of truth. Redis holds only disposable state
    # and a bounded public-profile cache. The password belongs in REDIS_URL (or
    # a local, Git-ignored .env) and is never logged.
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="Redis URL (redis:// or rediss://).",
    )
    redis_socket_connect_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30,
        description="Maximum time to establish a Redis connection.",
    )
    redis_socket_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30,
        description="Maximum time to wait for a Redis command.",
    )
    redis_health_check_interval_seconds: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="How often the client checks an idle pooled connection.",
    )
    redis_key_prefix: str = Field(
        default="messenger",
        min_length=1,
        max_length=64,
        description="Namespace prepended to every Redis key.",
    )
    cache_enabled: bool = Field(
        default=True,
        description="Enable the disposable public-profile cache.",
    )
    profile_cache_ttl_seconds: int = Field(
        default=60,
        ge=1,
        le=86400,
        description="Lifetime of a cached public profile.",
    )
    rate_limit_failure_policy: Literal["open", "closed"] = Field(
        default="closed",
        description=(
            "Behaviour when Redis is unavailable for rate limiting: 'closed' "
            "returns 503 (safer), 'open' allows the request."
        ),
    )

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

    # --- Shared rate limiting (Redis-backed, see app/core/rate_limit.py) ---
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable the Redis-backed shared rate limiter.",
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

    # --- Media (uploaded photos and files) ---
    # ``local`` keeps development and the original test-suite contract working.
    # ``minio`` is the durable object-storage backend used by Compose. The HTTP
    # contract is identical; only the storage implementation changes.
    media_storage_backend: str = Field(
        default="local",
        description="Media storage backend: 'local' or 'minio'.",
    )
    media_storage_path: str = Field(
        default="var/media",
        description="Directory for locally stored media (not inside the app package).",
    )
    # Per-file ceiling. Enforced before content validation and again by the
    # bounded read, so a client cannot force the server to buffer more.
    media_max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1,
        le=200 * 1024 * 1024,
        description="Maximum size of one uploaded file in bytes.",
    )
    media_max_files_per_request: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Maximum number of files accepted by one upload request.",
    )
    # Pictures are never decoded (no Pillow, no decompression bombs); only the
    # header dimensions are read. Absurd dimensions are refused up front.
    media_max_image_dimension: int = Field(
        default=20_000,
        ge=1,
        le=100_000,
        description="Maximum accepted image width/height from file headers.",
    )

    # --- MinIO object storage (MEDIA_STORAGE_BACKEND=minio) ---
    # ``minio_endpoint`` is the container-to-container (or local test) address;
    # it must not include a scheme. ``minio_public_endpoint`` is the address used
    # only to sign short-lived download URLs, so it may differ when the API runs
    # in Docker and the client reaches MinIO through a published port.
    minio_endpoint: str = Field(
        default="127.0.0.1:9000",
        description="MinIO API endpoint as host:port (no scheme).",
    )
    minio_public_endpoint: str = Field(
        default="127.0.0.1:9000",
        description="Endpoint used when signing client-facing presigned URLs.",
    )
    minio_access_key: str = Field(
        default="",
        description="MinIO application access key (not the root account).",
    )
    minio_secret_key: SecretStr = Field(
        default=SecretStr(""),
        description="MinIO application secret key (environment only).",
    )
    minio_bucket: str = Field(
        default="messenger-media",
        description="Private bucket holding uploaded media.",
    )
    minio_secure: bool = Field(
        default=False,
        description="Use HTTPS for MinIO connections and presigned URLs.",
    )
    minio_region: str = Field(
        default="us-east-1",
        description="S3 region used by the MinIO client.",
    )
    minio_key_prefix: str = Field(
        default="",
        description=(
            "Optional object-name prefix for this application/environment. "
            "A trailing slash is added automatically."
        ),
    )
    minio_presigned_url_expires_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Lifetime of a presigned download URL (30-3600 seconds).",
    )
    minio_auto_create_bucket: bool = Field(
        default=False,
        description=(
            "Create the bucket at startup when missing. Compose uses a separate "
            "initialization service instead; enable only for controlled setups."
        ),
    )
    minio_connect_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30,
        description="Maximum time to establish a MinIO connection.",
    )
    minio_read_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        description="Maximum time to wait for a MinIO response.",
    )
    minio_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Retries for transient MinIO failures.",
    )

    # --- Realtime (WebSocket) ---
    # Seconds a freshly accepted socket may wait for its auth frame. Kept short
    # on purpose: an unauthenticated socket only consumes a pending slot.
    ws_auth_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    # How often the server sends an application-level ``ping``.
    ws_heartbeat_interval_seconds: float = Field(default=30.0, ge=0.01, le=3600)
    # A client that sends nothing for this long is disconnected and cleaned up.
    ws_idle_timeout_seconds: float = Field(default=90.0, gt=0, le=7200)
    # How often an open socket re-validates its token and account state,
    # independently of the frames it sends. Revocation is therefore bounded by
    # this interval (plus scheduling), not instantaneous. The lower bound keeps
    # a misconfiguration from turning the heartbeat into a database query loop.
    ws_token_recheck_interval_seconds: float = Field(default=30.0, ge=0.01, le=3600)
    # Devices/tabs a single account may keep open at once. The cap rejects the
    # new connection; it never evicts a working one.
    ws_max_connections_per_user: int = Field(default=5, ge=1, le=100)
    # Pre-authentication sockets: a global ceiling and a per-peer ceiling.
    ws_max_pending_connections: int = Field(default=64, ge=1, le=10000)
    ws_max_pending_connections_per_ip: int = Field(default=8, ge=1, le=1000)
    # Raw text-frame ceiling. Larger frames are rejected before JSON parsing;
    # the real body limit (MESSAGE_MAX_LENGTH) is applied afterwards.
    ws_max_frame_bytes: int = Field(default=65536, ge=1024, le=1048576)
    # Per-account message budget over the socket (pings are not counted).
    ws_message_rate_limit_attempts: int = Field(default=60, ge=1)
    ws_message_rate_limit_window_seconds: int = Field(default=60, ge=1)
    # Typing and read-receipt frames are throttled *per connection* so a buggy
    # client cannot turn them into a database write loop. ``is_typing=false`` is
    # never throttled, so a stop is always delivered.
    ws_typing_min_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    ws_typing_expiry_seconds: int = Field(default=5, ge=1, le=60)
    ws_read_min_interval_seconds: float = Field(default=0.5, gt=0, le=60)

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
    media_rate_limit_attempts: int = Field(default=60, ge=1)
    media_rate_limit_window_seconds: int = Field(default=60, ge=1)

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

    @field_validator("media_storage_backend")
    @classmethod
    def _validate_media_backend(cls, value: str) -> str:
        """Only advertise backends that actually have an implementation."""
        backend = value.strip().lower()
        if backend not in ALLOWED_MEDIA_STORAGE_BACKENDS:
            allowed = ", ".join(sorted(ALLOWED_MEDIA_STORAGE_BACKENDS))
            raise ValueError(f"media_storage_backend must be one of: {allowed}")
        return backend

    @field_validator("media_storage_path")
    @classmethod
    def _validate_media_storage_path(cls, value: str) -> str:
        path = value.strip()
        if not path:
            raise ValueError("media_storage_path must not be empty")
        return path

    @field_validator("minio_endpoint", "minio_public_endpoint")
    @classmethod
    def _validate_minio_endpoint(cls, value: str) -> str:
        """Accept only a bare ``host:port`` (the SDK adds the scheme)."""
        endpoint = value.strip().rstrip("/")
        if not endpoint:
            raise ValueError("MinIO endpoint must not be empty")
        if "://" in endpoint:
            raise ValueError("MinIO endpoint must be host:port without a scheme")
        if any(character.isspace() for character in endpoint):
            raise ValueError("MinIO endpoint must not contain whitespace")
        parsed = urlsplit(f"//{endpoint}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("MinIO endpoint has an invalid port") from exc
        if not parsed.hostname or port is None:
            raise ValueError("MinIO endpoint must be host:port")
        if not 1 <= port <= 65535:
            raise ValueError("MinIO endpoint port must be between 1 and 65535")
        if parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("MinIO endpoint must be host:port without a path")
        return endpoint

    @field_validator("minio_bucket")
    @classmethod
    def _validate_minio_bucket(cls, value: str) -> str:
        bucket = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError(
                "minio_bucket must be 3-63 lowercase letters, digits, dots or "
                "hyphens and start/end with a letter or digit"
            )
        if ".." in bucket or ".-" in bucket or "-." in bucket:
            raise ValueError("minio_bucket contains an invalid dot/hyphen sequence")
        return bucket

    @field_validator("minio_key_prefix")
    @classmethod
    def _validate_minio_key_prefix(cls, value: str) -> str:
        prefix = value.strip().strip("/")
        if not prefix:
            return ""
        if ".." in prefix or "//" in prefix:
            raise ValueError("minio_key_prefix must not contain '..' or '//'")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", prefix):
            raise ValueError("minio_key_prefix contains unsupported characters")
        return f"{prefix}/"

    @model_validator(mode="after")
    def _validate_minio_credentials(self) -> "Settings":
        """Require application credentials whenever MinIO is selected."""
        if self.media_storage_backend != "minio":
            return self
        if not self.minio_access_key.strip():
            raise ValueError(
                "minio_access_key is required when media_storage_backend='minio'"
            )
        secret = self.minio_secret_key.get_secret_value()
        if len(secret) < 8:
            raise ValueError(
                "minio_secret_key must be at least 8 characters when "
                "media_storage_backend='minio'"
            )
        environment = self.app_env.strip().lower()
        if environment not in _LOCAL_ENVIRONMENTS and any(
            marker in secret.lower() for marker in INSECURE_SECRET_MARKERS
        ):
            raise ValueError(
                "minio_secret_key looks like an example value and is not allowed here"
            )
        return self

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: str) -> str:
        url = value.strip()
        if not url:
            raise ValueError("redis_url must not be empty")
        parsed = urlsplit(url)
        if parsed.scheme not in ALLOWED_REDIS_SCHEMES:
            allowed = ", ".join(sorted(ALLOWED_REDIS_SCHEMES))
            raise ValueError(f"redis_url must use one of: {allowed}")
        if not parsed.hostname:
            raise ValueError("redis_url must include a host")
        return url

    @field_validator("redis_key_prefix")
    @classmethod
    def _validate_redis_key_prefix(cls, value: str) -> str:
        prefix = value.strip().strip(":")
        if not prefix:
            raise ValueError("redis_key_prefix must not be empty")
        if any(character.isspace() for character in prefix):
            raise ValueError("redis_key_prefix must not contain whitespace")
        return prefix

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
