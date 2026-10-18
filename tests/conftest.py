"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os

# The JWT signing key is environment-only (no usable default in Settings), and
# ``import app.main`` builds the app eagerly. Provide a test key before that
# import so the suite never depends on a developer's local `.env`.
TEST_JWT_SECRET = "test-secret-key-0123456789abcdefghijklmnopqrstuvwxyz"
os.environ.setdefault("JWT_SECRET_KEY", TEST_JWT_SECRET)

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.main import create_app  # noqa: E402

# Importing the models registers every table on Base.metadata.
import app.db.models  # noqa: E402, F401


@pytest.fixture()
def db_url(tmp_path) -> str:
    """Path to an isolated, throw-away SQLite database for a single test."""
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture()
def test_settings(db_url: str) -> Settings:
    """Deterministic settings used by the test-suite.

    The database URL points at a temporary file so tests never touch the real
    ``messenger.db`` and always start from a clean state. The login limiter is
    left enabled but with a generous budget; the rate-limit tests build their own
    app with tight limits.
    """
    return Settings(
        app_name="Messenger API",
        app_env="test",
        app_debug=True,
        app_version="0.1.0",
        api_v1_prefix="/api/v1",
        cors_origins=["http://localhost:3000"],
        database_url=db_url,
        jwt_secret_key=TEST_JWT_SECRET,
        access_token_expire_minutes=15,
        rate_limit_enabled=True,
        login_rate_limit_attempts=1000,
        login_rate_limit_window_seconds=60,
    )


@pytest.fixture()
def app(test_settings: Settings):
    """A freshly built application instance bound to the test settings.

    The full schema is created on the temporary database so tests exercise real
    queries without paying for a migration run in every test (the migrations
    themselves are verified separately in ``tests/test_migrations.py``).
    """
    application = create_app(settings=test_settings)
    Base.metadata.create_all(bind=application.state.database.engine)
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


@pytest.fixture()
def db_session(database: Database) -> Iterator:
    """A raw session bound to the test database, for direct assertions."""
    session = database.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Make sure the cached settings do not leak between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
