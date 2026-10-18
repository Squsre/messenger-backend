"""Health / readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.schemas.health import HealthResponse

router = APIRouter()


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
