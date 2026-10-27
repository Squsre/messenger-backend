"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import redis
from dotenv import dotenv_values
from redis.exceptions import RedisError

# The JWT signing key is environment-only (no usable default in Settings), and
# ``import app.main`` builds the app eagerly. Provide a test key before that
# import so the suite never depends on a developer's local `.env`.
TEST_JWT_SECRET = "test-secret-key-0123456789abcdefghijklmnopqrstuvwxyz"
os.environ.setdefault("JWT_SECRET_KEY", TEST_JWT_SECRET)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import DatabaseSettings, Settings, get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.email import EmailMessage  # noqa: E402

# Importing the models registers every table on Base.metadata.
import app.db.models  # noqa: E402, F401

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_VALUES = dotenv_values(PROJECT_ROOT / ".env")

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"

#: Satisfies the documented password policy.
STRONG_PASSWORD = "Str0ng-Passw0rd!"


def _configured_database_url(name: str) -> str:
    """Read a PostgreSQL URL from the process environment or the local .env."""
    value = os.getenv(name) or _ENV_VALUES.get(name, "")
    if not value:
        raise pytest.UsageError(
            f"{name} is required. Point it at a disposable PostgreSQL database "
            "(for example messenger_test) before running the test suite."
        )
    return DatabaseSettings(database_url=value).database_url


TEST_DATABASE_URL = _configured_database_url("TEST_DATABASE_URL")
_APP_DATABASE_URL = _configured_database_url("DATABASE_URL")


def _configured_redis_url(name: str) -> str:
    """Read a Redis URL from the process environment or the local .env."""
    value = os.getenv(name) or _ENV_VALUES.get(name, "")
    if not value:
        if name == "TEST_REDIS_URL":
            base = os.getenv("REDIS_URL") or _ENV_VALUES.get("REDIS_URL", "")
            if base:
                # Default the disposable tests to database 15 on the same server.
                parsed = urlsplit(base)
                value = urlunsplit(parsed._replace(path="/15"))
        if not value:
            raise pytest.UsageError(
                f"{name} is required. Point it at a disposable Redis database "
                "(for example database 15) before running the test suite."
            )
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise pytest.UsageError(f"{name} must be a redis:// or rediss:// URL")
    return value.strip()


TEST_REDIS_URL = _configured_redis_url("TEST_REDIS_URL")
_APP_REDIS_URL = _configured_redis_url("REDIS_URL")


def _same_server_database(left: str, right: str) -> bool:
    left_url = make_url(left)
    right_url = make_url(right)
    return (
        left_url.host == right_url.host
        and left_url.port == right_url.port
        and left_url.database == right_url.database
    )


def _same_redis_database(left: str, right: str) -> bool:
    left_url = urlsplit(left)
    right_url = urlsplit(right)
    return (
        left_url.hostname == right_url.hostname
        and (left_url.port or 6379) == (right_url.port or 6379)
        and (left_url.path or "/0") == (right_url.path or "/0")
    )


if _same_server_database(TEST_DATABASE_URL, _APP_DATABASE_URL):
    raise pytest.UsageError(
        "TEST_DATABASE_URL must point at a separate database from DATABASE_URL; "
        "the test suite creates and drops schemas and must not touch development data."
    )

if _same_redis_database(TEST_REDIS_URL, _APP_REDIS_URL):
    raise pytest.UsageError(
        "TEST_REDIS_URL must point at a separate Redis database from REDIS_URL; "
        "the test suite only deletes its own key prefix, but isolation is required."
    )


def _ensure_test_database(url: str) -> None:
    """Create the disposable test database when it does not exist yet.

    The test database is created next to the development database, but is a
    separate database. Every test then runs in its own short-lived schema, so no
    table in the development database is ever touched.
    """
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg":
        raise pytest.UsageError(
            "TEST_DATABASE_URL must use postgresql+psycopg://..."
        )
    database = parsed.database
    if not database or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise pytest.UsageError(
            "TEST_DATABASE_URL must contain a simple PostgreSQL database name"
        )

    admin_engine = create_engine(
        parsed.set(database="postgres", query={}),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).scalar_one_or_none()
            if exists is None:
                # The identifier was validated above and is never user input.
                connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin_engine.dispose()


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


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Ensure the disposable PostgreSQL test database exists."""
    _ensure_test_database(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def test_redis_url() -> str:
    """Ensure the disposable Redis test database is reachable."""
    client = redis.Redis.from_url(
        TEST_REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        client.ping()
    except RedisError as exc:
        raise pytest.UsageError(
            "TEST_REDIS_URL is not reachable. Start Redis with "
            "'docker compose up -d redis' before running the test suite."
        ) from exc
    finally:
        client.close()
    return TEST_REDIS_URL


@pytest.fixture()
def redis_namespace(test_redis_url: str) -> Iterator[str]:
    """A unique Redis key prefix, removed after the test.

    Tests share one disposable Redis database but never see each other's keys.
    The cleanup only deletes this prefix; it never runs ``FLUSHDB`` and therefore
    cannot touch another application or the development namespace.
    """
    prefix = f"messenger-test-{uuid.uuid4().hex}"
    yield prefix

    client = redis.Redis.from_url(
        TEST_REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        for key in client.scan_iter(match=f"{prefix}:*", count=500):
            client.delete(key)
    finally:
        client.close()


@pytest.fixture()
def db_url(test_database_url: str) -> Iterator[str]:
    """Create an isolated PostgreSQL schema for a single test.

    The schema is put first on ``search_path`` for the test application's
    connections. It is dropped after the test, so tests cannot see each other's
    rows and the development database is never touched.
    """
    schema = f"test_{uuid.uuid4().hex}"
    admin_engine = create_engine(test_database_url, pool_pre_ping=True)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        parsed = make_url(test_database_url)
        query = dict(parsed.query)
        query["options"] = f"-csearch_path={schema}"
        url = parsed.set(query=query).render_as_string(hide_password=False)
        yield url
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture()
def test_settings(db_url: str, redis_namespace: str, tmp_path) -> Settings:
    """Deterministic settings used by the test-suite.

    The database URL points at a throw-away PostgreSQL schema so tests never
    touch development data and always start from a clean state. The login
    limiter is left enabled but with a generous budget; the rate-limit tests
    build their own app with tight limits.

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
        redis_url=TEST_REDIS_URL,
        redis_key_prefix=redis_namespace,
        cache_enabled=True,
        profile_cache_ttl_seconds=60,
        rate_limit_failure_policy="closed",
        jwt_secret_key=TEST_JWT_SECRET,
        access_token_expire_minutes=15,
        rate_limit_enabled=True,
        login_rate_limit_attempts=1000,
        login_rate_limit_window_seconds=60,
        email_backend="disabled",
        email_from="no-reply@messenger.test",
        public_base_url="http://testserver",
        # Existing media tests use the isolated local backend. MinIO-specific
        # tests opt in explicitly and use a unique key prefix.
        media_storage_backend="local",
        media_storage_path=str(tmp_path / "media"),
        minio_key_prefix="",
    )


@pytest.fixture()
def app(test_settings: Settings):
    """A freshly built application instance bound to the test settings.

    The full schema is created in the test's isolated PostgreSQL schema so tests
    exercise real queries without paying for a migration run in every test (the
    migrations themselves are verified separately in ``tests/test_migrations.py``).
    """
    application = create_app(settings=test_settings)
    Base.metadata.create_all(bind=application.state.database.engine)
    yield application
    # Release pooled connections before the schema and namespace are dropped.
    application.state.redis_manager.close()
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

