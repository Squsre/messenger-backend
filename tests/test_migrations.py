"""Tests that exercise the real Alembic migrations against a temporary database."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.session import create_db_engine

# Importing the models registers them on Base.metadata.
import app.db.models  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Revision created by the phase-3 migration; the base point for data-preservation
#: tests, because it is the last schema that existed before this phase.
AUTH_REVISION = "0002_create_users_and_token_blocklist"
HEAD_REVISION = "0003_add_profiles_and_social"

_USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,32}$")

_INSERT_LEGACY_USER = text(
    "INSERT INTO users (email, hashed_password, is_active) "
    "VALUES (:email, :hashed_password, 1)"
)


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
        assert {
            "health_checks",
            "users",
            "token_blocklist",
            "user_sessions",
            "verification_tokens",
            "contacts",
            "blocks",
            "alembic_version",
        } <= tables

        columns = {column["name"] for column in inspector.get_columns("health_checks")}
        assert {"id", "status", "detail", "created_at", "updated_at"} <= columns

        user_columns = {column["name"] for column in inspector.get_columns("users")}
        assert {
            "id",
            "email",
            "hashed_password",
            "is_active",
            "created_at",
            "updated_at",
            "username",
            "display_name",
            "bio",
            "avatar_key",
            "profile_visibility",
            "show_last_seen",
            "last_seen_at",
            "email_verified_at",
            "token_version",
        } <= user_columns

        # ``username`` is filled for every account, so it is NOT NULL from the start.
        username_column = next(
            column for column in inspector.get_columns("users") if column["name"] == "username"
        )
        assert username_column["nullable"] is False

        blocklist_columns = {
            column["name"] for column in inspector.get_columns("token_blocklist")
        }
        assert {"id", "jti", "user_id", "token_type", "expires_at", "revoked_at"} <= (
            blocklist_columns
        )

        session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
        assert {
            "id",
            "session_id",
            "user_id",
            "user_agent",
            "ip_address",
            "created_at",
            "last_active_at",
            "expires_at",
            "revoked_at",
        } <= session_columns

        token_columns = {
            column["name"] for column in inspector.get_columns("verification_tokens")
        }
        assert {"id", "user_id", "purpose", "token_hash", "created_at", "expires_at", "used_at"} <= (
            token_columns
        )

        contact_columns = {column["name"] for column in inspector.get_columns("contacts")}
        assert {"id", "owner_id", "contact_id", "created_at"} <= contact_columns

        block_columns = {column["name"] for column in inspector.get_columns("blocks")}
        assert {"id", "blocker_id", "blocked_id", "created_at"} <= block_columns
    finally:
        engine.dispose()


def test_migration_head_is_the_expected_revision(migration_url: str) -> None:
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == HEAD_REVISION
    finally:
        engine.dispose()


def test_migrations_do_not_rewrite_earlier_revisions() -> None:
    """The phase-3 migration must stay byte-for-byte what it always was.

    A migration that was already applied somewhere must never be edited; new
    columns belong in a new revision. This is a cheap guard against that.
    """
    earlier = (PROJECT_ROOT / "migrations" / "versions" /
               "0002_create_users_and_token_blocklist.py").read_text(encoding="utf-8")
    assert 'revision: str = "0002_create_users_and_token_blocklist"' in earlier
    assert "0003" not in earlier


def test_email_uniqueness_is_enforced_by_the_database(migration_url: str) -> None:
    """Normalisation is applied in the app; the database is the hard guarantee."""
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        insert = text(
            "INSERT INTO users (email, username, hashed_password, is_active) "
            "VALUES (:email, :username, :hashed_password, 1)"
        )
        with engine.begin() as connection:
            connection.execute(
                insert,
                {
                    "email": "dup@example.com",
                    "username": "dup_one",
                    "hashed_password": "x",
                },
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    insert,
                    {
                        "email": "dup@example.com",
                        "username": "dup_two",
                        "hashed_password": "y",
                    },
                )
    finally:
        engine.dispose()


def test_username_uniqueness_is_enforced_by_the_database(migration_url: str) -> None:
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        insert = text(
            "INSERT INTO users (email, username, hashed_password, is_active) "
            "VALUES (:email, :username, :hashed_password, 1)"
        )
        with engine.begin() as connection:
            connection.execute(
                insert,
                {"email": "a@example.com", "username": "samename", "hashed_password": "x"},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    insert,
                    {"email": "b@example.com", "username": "samename", "hashed_password": "y"},
                )
    finally:
        engine.dispose()


def test_username_is_required_by_the_database(migration_url: str) -> None:
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(_INSERT_LEGACY_USER, {"email": "x@example.com", "hashed_password": "h"})
    finally:
        engine.dispose()


def test_self_contacts_and_self_blocks_are_rejected_by_the_database(migration_url: str) -> None:
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (email, username, hashed_password, is_active) "
                    "VALUES ('solo@example.com', 'solo', 'h', 1)"
                )
            )
            user_id = connection.execute(text("SELECT id FROM users")).scalar_one()

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO contacts (owner_id, contact_id) VALUES (:a, :a)"),
                    {"a": user_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO blocks (blocker_id, blocked_id) VALUES (:a, :a)"),
                    {"a": user_id},
                )
    finally:
        engine.dispose()


def test_upgrade_preserves_existing_accounts_and_backfills_usernames(
    migration_url: str,
) -> None:
    """Existing rows survive, and every one of them ends up with a unique handle.

    The three addresses below all sanitise to ``john_doe``, so the backfill has to
    de-duplicate deterministically instead of producing a collision.
    """
    config = _alembic_config(migration_url)
    command.upgrade(config, AUTH_REVISION)

    engine = create_db_engine(migration_url)
    legacy = [
        ("John.Doe@example.com", "hash-one"),
        ("john+doe@example.com", "hash-two"),
        ("john.doe@example.com", "hash-three"),
    ]
    try:
        with engine.begin() as connection:
            for email, hashed in legacy:
                connection.execute(
                    _INSERT_LEGACY_USER, {"email": email, "hashed_password": hashed}
                )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT email, hashed_password, username FROM users ORDER BY id")
            ).fetchall()

        assert [row.email for row in rows] == [email for email, _ in legacy]
        assert [row.hashed_password for row in rows] == [hashed for _, hashed in legacy]

        usernames = [row.username for row in rows]
        assert all(username and _USERNAME_PATTERN.match(username) for username in usernames)
        assert len(set(usernames)) == len(usernames)
        # Deterministic: derived from the email local part, de-duplicated by id.
        assert usernames == ["john_doe", "john_doe_2", "john_doe_3"]
    finally:
        engine.dispose()


def test_upgrade_is_idempotent_for_the_backfill(migration_url: str) -> None:
    """Running the backfill twice cannot change the names it produced."""
    config = _alembic_config(migration_url)
    command.upgrade(config, AUTH_REVISION)

    engine = create_db_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                _INSERT_LEGACY_USER, {"email": "keep.me@example.com", "hashed_password": "h"}
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.connect() as connection:
            first = connection.execute(text("SELECT username FROM users")).scalar_one()
    finally:
        engine.dispose()

    # Downgrading and re-upgrading must land on the same derived name.
    command.downgrade(config, AUTH_REVISION)
    command.upgrade(config, "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.connect() as connection:
            second = connection.execute(text("SELECT username FROM users")).scalar_one()
    finally:
        engine.dispose()

    assert first == second == "keep_me"


def test_upgrade_then_downgrade_to_base(migration_url: str) -> None:
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_db_engine(migration_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables <= {"alembic_version"}
    finally:
        engine.dispose()


def test_downgrade_one_step_removes_only_the_new_tables(migration_url: str) -> None:
    """Rolling back this phase keeps the phase-3 schema intact."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "-1")

    engine = create_db_engine(migration_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"health_checks", "users", "token_blocklist"} <= tables
        assert not {"user_sessions", "verification_tokens", "contacts", "blocks"} & tables

        user_columns = {column["name"] for column in inspector.get_columns("users")}
        assert "username" not in user_columns
        assert "token_version" not in user_columns
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
