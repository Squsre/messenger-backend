"""Reusable FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings, get_settings


def get_app_settings(request: Request) -> Settings:
    """Return the settings the running application was built with.

    Falls back to the process-wide cached settings when the application did not
    attach its own instance (for example inside ad-hoc scripts).
    """
    return getattr(request.app.state, "settings", None) or get_settings()
