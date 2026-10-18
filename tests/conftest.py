"""Pytest configuration and shared fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.session import Database
from app.main import create_app


@pytest.fixture()
def db_url(tmp_path) -> str:
    """Path to an isolated, throw-away SQLite database for a single test."""
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture()
def test_settings(db_url: str) -> Settings:
    """Deterministic settings used by the test-suite.

    The database URL points at a temporary file so tests never touch the real
    ``messenger.db`` and always start from a clean state.
    """
    return Settings(
        app_name="Messenger API",
        app_env="test",
        app_debug=True,
        app_version="0.1.0",
        api_v1_prefix="/api/v1",
        cors_origins=["http://localhost:3000"],
        database_url=db_url,
    )


@pytest.fixture()
def app(test_settings: Settings):
    """A freshly built application instance bound to the test settings."""
    application = create_app(settings=test_settings)
    yield application
    # Release any open connections so Windows can delete the temp directory.
    application.state.database.dispose()


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    """A ``TestClient`` wrapping the test application."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def database(app) -> Database:
    """The isolated database owned by the test application."""
    return app.state.database


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Make sure the cached settings do not leak between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
