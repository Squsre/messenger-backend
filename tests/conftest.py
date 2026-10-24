"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import re

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
from app.services.email import EmailMessage  # noqa: E402

# Importing the models registers every table on Base.metadata.
import app.db.models  # noqa: E402, F401

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"

#: Satisfies the documented password policy.
STRONG_PASSWORD = "Str0ng-Passw0rd!"


class CapturingEmailService:
    """Test transport: records messages instead of sending them.

    Only the *transport* is replaced. Token generation, hashing, storage,
    expiry and single-use enforcement all run for real against the database, so
    these tests exercise the production code paths.
    """

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)

    @property
    def last(self) -> EmailMessage:
        assert self.messages, "no email was sent"
        return self.messages[-1]

    @property
    def last_token(self) -> str:
        """The token from the most recently sent message's link."""
        return extract_token(self.last.body)

    def tokens(self) -> list[str]:
        return [extract_token(message.body) for message in self.messages]

    def reset(self) -> None:
        self.messages.clear()


def extract_token(body: str) -> str:
    """Pull the ``token`` query parameter out of a rendered email body."""
    match = re.search(r"[?&]token=([A-Za-z0-9_\-]+)", body)
    assert match is not None, f"no token found in message body: {body!r}"
    return match.group(1)


class ApiHelper:
    """Small wrapper around the register/login dance, shared by the new tests."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def register(
        self,
        email: str,
        password: str = STRONG_PASSWORD,
        *,
        username: str | None = None,
    ) -> dict:
        payload: dict[str, str] = {"email": email, "password": password}
        if username is not None:
            payload["username"] = username
        response = self.client.post(REGISTER_URL, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    def login(self, email: str, password: str = STRONG_PASSWORD) -> str:
        response = self.client.post(LOGIN_URL, json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def headers(self, email: str, password: str = STRONG_PASSWORD) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.login(email, password)}"}

    def create_user(
        self,
        email: str,
        password: str = STRONG_PASSWORD,
        *,
        username: str | None = None,
    ) -> tuple[dict, dict[str, str]]:
        account = self.register(email, password, username=username)
        return account, self.headers(email, password)

    def set_profile(self, headers: dict[str, str], **changes: object) -> dict:
        response = self.client.patch("/api/v1/users/me", json=changes, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture()
def db_url(tmp_path) -> str:
    """Path to an isolated, throw-away SQLite database for a single test."""
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture()
def test_settings(db_url: str, tmp_path) -> Settings:
    """Deterministic settings used by the test-suite.

    The database URL points at a temporary file so tests never touch the real
    ``messenger.db`` and always start from a clean state. The login limiter is
    left enabled but with a generous budget; the rate-limit tests build their own
    app with tight limits.

    Email delivery is ``disabled`` so a test can never write an outbox file into
    the repository; tests that need the message swap in
    :class:`CapturingEmailService` (see the ``outbox`` fixture).
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
        email_backend="disabled",
        email_from="no-reply@messenger.test",
        public_base_url="http://testserver",
        # Never write uploads into the repository during tests.
        media_storage_path=str(tmp_path / "media"),
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


@pytest.fixture()
def api(client) -> ApiHelper:
    """Register/login helpers bound to the test client."""
    return ApiHelper(client)


@pytest.fixture()
def outbox(app) -> CapturingEmailService:
    """Capture outgoing email for the duration of one test.

    Installed on ``app.state`` so the production dependency resolution path is
    the one under test.
    """
    service = CapturingEmailService()
    app.state.email_service = service
    return service


def user_id_for(db_session, email: str) -> int:
    """Look up an account id by email using the raw test session."""
    from sqlalchemy import select

    from app.db.models import User

    return int(db_session.execute(select(User.id).where(User.email == email)).scalar_one())


def deactivate_user(db_session, email: str) -> None:
    """Disable an account directly in the database (no API does this yet)."""
    from sqlalchemy import select

    from app.db.models import User

    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    user.is_active = False
    db_session.commit()


def set_email_verified(db_session, email: str) -> None:
    """Mark an account's address as confirmed without going through a token."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db.models import User

    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    user.email_verified_at = datetime.now(timezone.utc)
    db_session.commit()

