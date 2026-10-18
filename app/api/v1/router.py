"""Aggregate router for API version 1.

Every feature module exposes its own ``router``; they are all mounted here so
that :mod:`app.main` only needs to include a single router.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
