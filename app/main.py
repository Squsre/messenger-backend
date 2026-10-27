"""Application entry point.

Creates and configures the FastAPI application. The factory pattern is used so
tests (and future workers) can build isolated app instances.

Run locally with::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.rate_limit import RedisSlidingWindowRateLimiter
from app.db.session import Database
from app.schemas.health import RootResponse
from app.services.cache import CacheService
from app.services.email import build_email_service
from app.services.realtime import ConnectionManager
from app.services.redis import RedisManager
from app.services.storage import build_storage

logger = logging.getLogger(__name__)

# Methods and headers the API actually uses. Keeping these explicit (instead of
# ``*``) avoids advertising capabilities the service does not have.
CORS_ALLOWED_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type", "Accept"]

OPENAPI_TAGS = [
    {"name": "meta", "description": "Service metadata."},
    {"name": "health", "description": "Liveness and readiness probes."},
    {
        "name": "auth",
        "description": "Registration, login, current user and logout.",
    },
    {
        "name": "account",
        "description": (
            "Account security: email verification, password change and reset, "
            "and device session management."
        ),
    },
    {
        "name": "users",
        "description": "Profiles, privacy settings and user search.",
    },
    {
        "name": "contacts",
        "description": "One-directional contact lists.",
    },
    {
        "name": "blocks",
        "description": "Blocking users; enforced across profiles, search and contacts.",
    },
    {
        "name": "chat",
        "description": (
            "One-to-one private conversations and messages, including delivery "
            "and read status, unread counts and read markers. Realtime delivery "
            "and ephemeral typing indicators use the WebSocket at /api/v1/ws."
        ),
    },
    {
        "name": "groups",
        "description": (
            "Private and public groups: ownership and admin roles, invitations, "
            "membership, public discovery and group messages with per-member "
            "delivery/read receipts. Group events use the same WebSocket."
        ),
    },
    {
        "name": "media",
        "description": (
            "Authenticated upload, metadata, download, short-lived direct URL, "
            "attachment and deletion of conversation-scoped photos and files. "
            "Binaries live in private MinIO object storage (or local disk in "
            "development), never in the database."
        ),
    },
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured FastAPI application."""
    settings = settings or get_settings()

    # The database and Redis instances are created per application so tests can
    # supply isolated resources through their own settings.
    database = Database(settings.database_url, echo=settings.db_echo)
    redis_manager = RedisManager(settings)
    cache = CacheService(redis_manager, enabled=settings.cache_enabled)

    def _limiter(
        scope: str, attempts: int, window_seconds: int
    ) -> RedisSlidingWindowRateLimiter:
        return RedisSlidingWindowRateLimiter(
            redis_manager,
            scope=scope,
            max_requests=attempts,
            window_seconds=window_seconds,
            failure_policy=settings.rate_limit_failure_policy,
        )

    # One shared limiter per scope: a noisy feature (say search) cannot spend
    # another feature's budget. Counters live in Redis, so every worker sees the
    # same budget. "login" keeps its historical attribute below as well.
    login_rate_limiter = _limiter(
        "login",
        settings.login_rate_limit_attempts,
        settings.login_rate_limit_window_seconds,
    )
    rate_limiters: dict[str, RedisSlidingWindowRateLimiter] = {
        "login": login_rate_limiter,
        "email_verification": _limiter(
            "email_verification",
            settings.email_rate_limit_attempts,
            settings.email_rate_limit_window_seconds,
        ),
        "email_verify_confirm": _limiter(
            "email_verify_confirm",
            settings.email_rate_limit_attempts,
            settings.email_rate_limit_window_seconds,
        ),
        "password_reset": _limiter(
            "password_reset",
            settings.password_reset_rate_limit_attempts,
            settings.password_reset_rate_limit_window_seconds,
        ),
        "password_reset_confirm": _limiter(
            "password_reset_confirm",
            settings.password_reset_rate_limit_attempts,
            settings.password_reset_rate_limit_window_seconds,
        ),
        "search": _limiter(
            "search",
            settings.search_rate_limit_attempts,
            settings.search_rate_limit_window_seconds,
        ),
        "contact": _limiter(
            "contact",
            settings.contact_rate_limit_attempts,
            settings.contact_rate_limit_window_seconds,
        ),
        "chat": _limiter(
            "chat",
            settings.chat_rate_limit_attempts,
            settings.chat_rate_limit_window_seconds,
        ),
        "media": _limiter(
            "media",
            settings.media_rate_limit_attempts,
            settings.media_rate_limit_window_seconds,
        ),
        "ws": _limiter(
            "ws",
            settings.ws_message_rate_limit_attempts,
            settings.ws_message_rate_limit_window_seconds,
        ),
    }

    # Live WebSocket sockets. In-process only; see app/services/realtime.py and
    # the README for the multi-worker limitation.
    ws_manager = ConnectionManager(
        max_per_user=settings.ws_max_connections_per_user,
        max_pending_connections=settings.ws_max_pending_connections,
        max_pending_per_key=settings.ws_max_pending_connections_per_ip,
    )

    email_service = build_email_service(settings)
    # Local disk or MinIO, selected by configuration; endpoints only see the
    # StorageService protocol.
    storage = build_storage(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("Database engine ready (%s)", database.safe_url)
        logger.info("Redis client configured (%s)", redis_manager.safe_url)
        logger.info("Media storage backend configured (%s)", storage.backend_name)
        ensure_bucket = getattr(storage, "ensure_bucket", None)
        if (
            settings.media_storage_backend == "minio"
            and settings.minio_auto_create_bucket
        ):
            try:
                ensure_bucket()
            except Exception:
                logger.exception("Could not initialize the MinIO bucket at startup")
        if not storage.health():
            logger.warning(
                "Media storage is not ready at startup; uploads/downloads will fail "
                "until the configured bucket or directory is available"
            )
        if not redis_manager.ping():
            logger.warning(
                "Redis is not reachable at startup; cache will fail open and rate "
                "limiting will follow RATE_LIMIT_FAILURE_POLICY=%s",
                settings.rate_limit_failure_policy,
            )
        try:
            yield
        finally:
            # Tell every connected client the server is going away before the
            # database and Redis resources are released.
            await ws_manager.close_all(code=1001, reason="Server is shutting down")
            storage.close()
            redis_manager.close()
            database.dispose()
            logger.info("Media storage, Redis client and database engine disposed")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version or __version__,
        description=(
            "Backend for the messenger project.\n\n"
            "Realtime chat is available over WebSocket at `/api/v1/ws`. Because "
            "OpenAPI 3.0 cannot describe WebSocket frames, that protocol is "
            "documented in the README (see \"Realtime chat (WebSocket)\")."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        debug=settings.app_debug,
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )

    # Expose shared resources to dependencies via `app.state`.
    app.state.settings = settings
    app.state.database = database
    app.state.redis_manager = redis_manager
    app.state.redis = redis_manager
    app.state.cache = cache
    app.state.login_rate_limiter = login_rate_limiter
    app.state.rate_limiters = rate_limiters
    app.state.email_service = email_service
    app.state.storage = storage
    app.state.ws_manager = ws_manager

    if settings.cors_origins:
        # ``allow_credentials`` stays off: authentication uses Bearer tokens,
        # not cookies, so browsers never need to send credentials implicitly.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=CORS_ALLOWED_METHODS,
            allow_headers=CORS_ALLOWED_HEADERS,
        )

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", response_model=RootResponse, tags=["meta"], summary="API banner")
    async def root() -> RootResponse:
        """Return a small banner pointing at the docs and health endpoints."""
        return RootResponse(
            name=settings.app_name,
            version=settings.app_version,
            docs_url="/docs",
            health_url=f"{settings.api_v1_prefix}/health",
        )

    logger.info(
        "Application '%s' v%s configured for '%s' (docs at /docs)",
        settings.app_name,
        settings.app_version,
        settings.app_env,
    )

    return app


app = create_app()
