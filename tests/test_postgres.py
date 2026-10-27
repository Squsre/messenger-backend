"""PostgreSQL integration tests.

The whole test suite runs against PostgreSQL. These tests additionally assert
dialect-specific behaviour: real migrations from an empty schema, the widened
Alembic version column, and PostgreSQL partial indexes/CHECK constraints.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.models import HealthCheck
from app.db.session import Database, create_db_engine

# Importing the models registers them on Base.metadata.
import app.db.models  # noqa: E402, F401

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # ConfigParser uses ``%`` for interpolation. The schema URL contains a
    # percent-encoded search_path, so escape it for the ini layer.
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return config


def test_postgres_connection_create_tables_and_crud(db_url: str) -> None:
    database = Database(db_url)
    try:
        with database.engine.connect() as connection:
            assert connection.dialect.name == "postgresql"
            assert connection.execute(text("SELECT 1")).scalar_one() == 1

        Base.metadata.create_all(bind=database.engine)
        tables = set(inspect(database.engine).get_table_names())
        assert {
            "health_checks",
            "users",
            "conversations",
            "conversation_members",
            "messages",
            "media",
            "group_invitations",
            "message_receipts",
        } <= tables

        with database.session() as session:
            session.add(HealthCheck(status="ok", detail="postgres crud"))

        with database.session() as session:
            stored = session.execute(
                select(HealthCheck).where(HealthCheck.detail == "postgres crud")
            ).scalar_one()
            assert stored.status == "ok"
            assert stored.id is not None
            assert stored.created_at is not None
    finally:
        database.dispose()


def test_postgres_migrations_from_empty_to_head_and_back(db_url: str) -> None:
    config = _alembic_config(db_url)

    command.upgrade(config, "head")

    engine = create_db_engine(db_url)
    try:
        inspector = inspect(engine)
        assert {
            "health_checks",
            "users",
            "token_blocklist",
            "user_sessions",
            "verification_tokens",
            "contacts",
            "blocks",
            "conversations",
            "conversation_members",
            "messages",
            "media",
            "group_invitations",
            "message_receipts",
            "alembic_version",
        } <= set(inspector.get_table_names())

        version_columns = {
            column["name"]: column
            for column in inspector.get_columns("alembic_version")
        }
        # The longest revision id is 38 characters; Alembic's default 32 would
        # make the first PostgreSQL upgrade fail.
        assert version_columns["version_num"]["type"].length >= 38

        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == "0009_media_storage_metadata"

            migration_context = MigrationContext.configure(connection)
            diff = compare_metadata(migration_context, Base.metadata)
            assert diff == [], f"PostgreSQL schema drift detected: {diff}"

        invitation_indexes = {
            index["name"] for index in inspector.get_indexes("group_invitations")
        }
        assert "uq_group_invitations_pending" in invitation_indexes
    finally:
        engine.dispose()

    command.downgrade(config, "base")

    engine = create_db_engine(db_url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()


def test_postgres_group_invariants_and_partial_unique_index(db_url: str) -> None:
    config = _alembic_config(db_url)
    command.upgrade(config, "head")

    engine = create_db_engine(db_url)
    try:
        with engine.begin() as connection:
            for email, username in (
                ("alice@example.com", "alice"),
                ("bob@example.com", "bob"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO users (email, username, hashed_password, is_active) "
                        "VALUES (:email, :username, 'h', TRUE)"
                    ),
                    {"email": email, "username": username},
                )
            connection.execute(
                text(
                    "INSERT INTO conversations "
                    "(pair_key, kind, name, visibility, owner_id) "
                    "VALUES ('group:postgres', 'group', 'Postgres Group', 'private', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_members "
                    "(conversation_id, user_id, role) VALUES (1, 1, 'owner')"
                )
            )

        invitation_insert = text(
            "INSERT INTO group_invitations "
            "(conversation_id, inviter_id, invitee_id, status) "
            "VALUES (1, 1, 2, :status)"
        )
        with engine.begin() as connection:
            connection.execute(invitation_insert, {"status": "pending"})

        # The PostgreSQL partial unique index must enforce one pending invite.
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(invitation_insert, {"status": "pending"})

        # A declined invitation frees the pair for a new pending invitation.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE group_invitations SET status = 'declined', "
                    "responded_at = CURRENT_TIMESTAMP WHERE id = 1"
                )
            )
            connection.execute(invitation_insert, {"status": "pending"})

        # Check constraints and one receipt per (message, member) also hold.
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(invitation_insert, {"status": "unknown"})
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO group_invitations "
                        "(conversation_id, inviter_id, invitee_id) "
                        "VALUES (1, 1, 1)"
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO messages (conversation_id, sender_id, body) "
                    "VALUES (1, 1, 'postgres group message')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO message_receipts (message_id, user_id) "
                    "VALUES (1, 2)"
                )
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO message_receipts (message_id, user_id) "
                        "VALUES (1, 2)"
                    )
                )
    finally:
        engine.dispose()
