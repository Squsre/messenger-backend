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
from app.db.session import Database
from app.schemas.health import RootResponse

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured FastAPI application."""
    settings = settings or get_settings()

    # The database instance is created per application so tests can supply an
    # isolated database through their own settings.
    database = Database(settings.database_url, echo=settings.db_echo)

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
    )

    # Expose shared resources to dependencies via `app.state`.
    app.state.settings = settings
    app.state.database = database

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
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
