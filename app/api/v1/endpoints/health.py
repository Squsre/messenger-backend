"""Health / readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter()


def _database_is_ready(database) -> bool:
    """Run a tiny query against the application's database."""
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
        return True
    except Exception:  # pragma: no cover - exercised by failure injection
        return False


def _storage_is_ready(storage) -> bool:
    """Check the configured media backend (bucket presence for MinIO)."""
    try:
        return bool(storage.health())
    except Exception:  # pragma: no cover - exercised by failure injection
        return False


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description="Return the current status of the service along with basic metadata.",
    responses={
        200: {
            "description": "Service is healthy.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "service": "Messenger API",
                        "version": "0.1.0",
                        "environment": "development",
                    }
                }
            },
        }
    },
)
async def health(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    """Report that the service is up and running."""
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness check",
    description=(
        "Check the dependencies needed to serve requests: PostgreSQL, Redis and "
        "the configured media storage backend (MinIO bucket or local directory). "
        "Returns 200 when all answer and 503 otherwise. The liveness endpoint "
        "at /health intentionally does not depend on any of them."
    ),
    responses={
        200: {"description": "All dependencies are reachable."},
        503: {"description": "At least one dependency is unavailable."},
    },
)
async def readiness(request: Request) -> JSONResponse:
    """Report dependency readiness without blocking the event loop."""
    database = request.app.state.database
    cache = request.app.state.cache
    storage = request.app.state.storage
    database_ok = await run_in_threadpool(_database_is_ready, database)
    redis_ok = await run_in_threadpool(cache.ping)
    storage_ok = await run_in_threadpool(_storage_is_ready, storage)

    checks = {
        "database": "ok" if database_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "storage": "ok" if storage_ok else "error",
    }
    payload = ReadinessResponse(
        status="ok" if database_ok and redis_ok and storage_ok else "degraded",
        checks=checks,
    )
    return JSONResponse(
        status_code=200 if payload.status == "ok" else 503,
        content=payload.model_dump(),
    )
