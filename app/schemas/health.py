"""Schemas describing the health endpoint responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Payload returned by ``GET /api/v1/health``."""

    status: Literal["ok"] = Field(description="Overall service status.", examples=["ok"])
    service: str = Field(description="Service name.", examples=["Messenger API"])
    version: str = Field(description="Running application version.", examples=["0.1.0"])
    environment: str = Field(description="Deployment environment.", examples=["development"])


class ReadinessResponse(BaseModel):
    """Payload returned by ``GET /api/v1/health/ready``."""

    status: Literal["ok", "degraded"] = Field(description="Overall readiness.")
    checks: dict[str, Literal["ok", "error"]] = Field(
        description="Per-dependency readiness results.",
        examples=[{"database": "ok", "redis": "ok"}],
    )


class RootResponse(BaseModel):
    """Small banner returned by the API root."""

    name: str = Field(description="Service name.", examples=["Messenger API"])
    version: str = Field(description="Running application version.", examples=["0.1.0"])
    docs_url: str = Field(description="Interactive documentation URL.", examples=["/docs"])
    health_url: str = Field(description="Health check URL.", examples=["/api/v1/health"])
