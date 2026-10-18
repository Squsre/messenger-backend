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
from app.core.rate_limit import SlidingWindowRateLimiter
from app.db.session import Database
from app.schemas.health import RootResponse

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
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured FastAPI application."""
    settings = settings or get_settings()

    # The database instance is created per application so tests can supply an
    # isolated database through their own settings.
    database = Database(settings.database_url, echo=settings.db_echo)

    # In-process login throttle. See app/core/rate_limit.py for its scope and
    # limitations (single process; a shared store is needed to scale out).
    login_rate_limiter = SlidingWindowRateLimiter(
        max_requests=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("Database engine ready (%s)", settings.database_url)
        try:
            yield
        finally:
            database.dispose()
            logger.info("Database engine disposed")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version or __version__,
        description="Backend for the messenger project.",
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
    app.state.login_rate_limiter = login_rate_limiter

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
