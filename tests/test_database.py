"""Tests for the database layer: connection, sessions and the get_db dependency."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import DatabaseSettings
from app.db.base import Base
from app.db.models import HealthCheck
from app.db.session import Database, create_db_engine, get_db


@pytest.fixture()
def schema(database: Database) -> None:
    """Create all tables on the temporary database."""
    Base.metadata.create_all(bind=database.engine)


def test_engine_connects(db_url: str) -> None:
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgres://user:pass@localhost:5432/messenger",
            "postgresql+psycopg://user:pass@localhost:5432/messenger",
        ),
        (
            "postgresql://user:pass@localhost:5432/messenger",
            "postgresql+psycopg://user:pass@localhost:5432/messenger",
        ),
        (
            "postgresql+psycopg://user:pass@localhost:5432/messenger",
            "postgresql+psycopg://user:pass@localhost:5432/messenger",
        ),
    ],
)
def test_postgres_url_shorthand_is_normalised(raw: str, expected: str) -> None:
    settings = DatabaseSettings(database_url=raw)
    assert settings.database_url == expected


def test_non_postgres_database_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="must use PostgreSQL"):
        DatabaseSettings(database_url="sqlite:///./legacy.db")

    with pytest.raises(ValueError, match="Only PostgreSQL"):
        create_db_engine("sqlite:///./legacy.db")


def test_safe_url_redacts_the_password() -> None:
    database = Database(
        "postgresql+psycopg://messenger:s3cret@127.0.0.1:5432/messenger"
    )
    try:
        assert database.safe_url == (
            "postgresql+psycopg://messenger:***@127.0.0.1:5432/messenger"
        )
    finally:
        database.dispose()


def test_tables_are_created(database: Database, schema: None) -> None:
    from sqlalchemy import inspect

    tables = inspect(database.engine).get_table_names()
    assert "health_checks" in tables


def test_create_and_read_record(database: Database, schema: None) -> None:
    """Write a row through one session and read it back through another."""
    with database.session() as session:
        session.add(HealthCheck(status="ok", detail="created by test"))

    with database.session() as session:
        stored = session.execute(
            select(HealthCheck).where(HealthCheck.detail == "created by test")
        ).scalar_one()
        assert stored.status == "ok"
        assert stored.id is not None
        assert stored.created_at is not None

        # update in a later session and confirm persistence
        stored.status = "degraded"

    with database.session() as session:
        refreshed = session.execute(
            select(HealthCheck).where(HealthCheck.detail == "created by test")
        ).scalar_one()
        assert refreshed.status == "degraded"


def test_session_rolls_back_on_error(database: Database, schema: None) -> None:
    with pytest.raises(RuntimeError):
        with database.session() as session:
            session.add(HealthCheck(status="rolled_back", detail="boom"))
            session.flush()
            raise RuntimeError("simulated failure")

    with database.session() as session:
        remaining = session.execute(select(HealthCheck)).scalars().all()
        assert remaining == []


def test_session_is_closed_after_use(database: Database, schema: None) -> None:
    session_ref: Session | None = None
    with database.session() as session:
        session_ref = session
        session.add(HealthCheck(status="ok", detail="closing"))
        session.flush()

    assert session_ref is not None
    # A closed session no longer has an active transaction and refuses new work.
    assert not session_ref.in_transaction()


def test_get_db_dependency_provides_working_session(database: Database) -> None:
    """The dependency yields a usable session and is ready for future endpoints."""
    Base.metadata.create_all(bind=database.engine)

    probe_app = FastAPI()
    probe_app.state.database = database

    @probe_app.post("/probe")
    def create_probe(db: Session = Depends(get_db)) -> dict[str, int]:
        record = HealthCheck(status="ok", detail="from dependency")
        db.add(record)
        db.flush()
        return {"id": record.id}

    @probe_app.get("/probe")
    def list_probes(db: Session = Depends(get_db)) -> dict[str, int]:
        rows = db.execute(select(HealthCheck)).scalars().all()
        return {"count": len(rows)}

    with TestClient(probe_app) as client:
        created = client.post("/probe")
        assert created.status_code == 200
        assert created.json()["id"] == 1
        assert client.get("/probe").json() == {"count": 1}
