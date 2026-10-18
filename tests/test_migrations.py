"""Tests that exercise the real Alembic migrations against a temporary database."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.db.session import create_db_engine

# Importing the models registers them on Base.metadata.
import app.db.models  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def migration_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"


def _alembic_config(db_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def test_upgrade_head_creates_schema(migration_url: str) -> None:
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "health_checks" in tables
        assert "alembic_version" in tables

        columns = {column["name"] for column in inspector.get_columns("health_checks")}
        assert {"id", "status", "detail", "created_at", "updated_at"} <= columns
    finally:
        engine.dispose()


def test_upgrade_then_downgrade(migration_url: str) -> None:
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_db_engine(migration_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "health_checks" not in tables
    finally:
        engine.dispose()


def test_migrations_match_models(migration_url: str) -> None:
    """After ``upgrade head`` there must be no drift between models and schema."""
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_engine(migration_url)
    try:
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(connection)
            diff = compare_metadata(migration_context, Base.metadata)
        assert diff == [], f"Schema drift detected: {diff}"
    finally:
        engine.dispose()
