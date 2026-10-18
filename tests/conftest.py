"""Pytest configuration and shared fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture()
def test_settings() -> Settings:
    """Deterministic settings used by the test-suite."""
    return Settings(
        app_name="Messenger API",
        app_env="test",
        app_debug=True,
        app_version="0.1.0",
        api_v1_prefix="/api/v1",
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture()
def app(test_settings: Settings):
    """A freshly built application instance bound to the test settings."""
    return create_app(settings=test_settings)


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    """A ``TestClient`` wrapping the test application."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Make sure the cached settings do not leak between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
