"""Tests that exercise the real Alembic migrations against PostgreSQL."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from app.db.base import Base
from app.db.session import create_db_engine

# Importing the models registers them on Base.metadata.
import app.db.models  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Revision created by the phase-3 migration; the base point for data-preservation
#: tests, because it is the last schema that existed before the profile phase.
AUTH_REVISION = "0002_create_users_and_token_blocklist"
HEAD_REVISION = "0008_add_groups"

_USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,32}$")

_INSERT_LEGACY_USER = text(
    "INSERT INTO users (email, hashed_password, is_active) "
    "VALUES (:email, :hashed_password, TRUE)"
)


@pytest.fixture()
def migration_url(db_url: str) -> str:
    """Use the test's isolated PostgreSQL schema for real migrations."""
    return db_url


def _alembic_config(db_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # ConfigParser uses ``%`` for interpolation. The schema URL contains a
    # percent-encoded search_path, so escape it for the ini layer.
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
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
            "conversations",
            "conversation_members",
            "messages",
            "media",
            "group_invitations",
            "message_receipts",
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

        conversation_columns = {
            column["name"] for column in inspector.get_columns("conversations")
        }
        assert {
            "id",
            "pair_key",
            "kind",
            "name",
            "description",
            "visibility",
            "owner_id",
            "last_message_at",
            "created_at",
            "updated_at",
        } <= conversation_columns

        member_columns = {
            column["name"] for column in inspector.get_columns("conversation_members")
        }
        assert {"id", "conversation_id", "user_id", "role", "created_at"} <= member_columns

        message_columns = {column["name"] for column in inspector.get_columns("messages")}
        assert {"id", "conversation_id", "sender_id", "body", "created_at"} <= message_columns
        assert "client_message_id" in message_columns
        assert {"delivered_at", "read_at"} <= message_columns
        body_column = next(
            column for column in inspector.get_columns("messages") if column["name"] == "body"
        )
        assert body_column["nullable"] is True

        media_columns = {
            column["name"] for column in inspector.get_columns("media")
        }
        assert {
            "id",
            "public_id",
            "storage_key",
            "original_filename",
            "content_type",
            "size_bytes",
            "width",
            "height",
            "status",
            "conversation_id",
            "uploader_id",
            "message_id",
            "created_at",
        } <= media_columns
        media_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("media")
        }
        assert {
            "ck_media_size_positive",
            "ck_media_dimensions_valid",
            "ck_media_status_allowed",
        } <= media_checks

        invitation_columns = {
            column["name"]
            for column in inspector.get_columns("group_invitations")
        }
        assert {
            "id",
            "conversation_id",
            "inviter_id",
            "invitee_id",
            "status",
            "created_at",
            "responded_at",
        } <= invitation_columns
        invitation_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("group_invitations")
        }
        assert {
            "ck_group_invitations_status_allowed",
            "ck_group_invitations_no_self_invite",
        } <= invitation_checks
        invitation_indexes = {
            index["name"] for index in inspector.get_indexes("group_invitations")
        }
        assert {
            "ix_group_invitations_conversation_status",
            "ix_group_invitations_invitee_status",
            "ix_group_invitations_inviter_id",
            "uq_group_invitations_pending",
        } <= invitation_indexes

        receipt_columns = {
            column["name"] for column in inspector.get_columns("message_receipts")
        }
        assert {
            "id",
            "message_id",
            "user_id",
            "delivered_at",
            "read_at",
        } <= receipt_columns
        receipt_indexes = {
            index["name"] for index in inspector.get_indexes("message_receipts")
        }
        assert {
            "ix_message_receipts_user_id_read_at",
            "ix_message_receipts_message_id_read_at",
        } <= receipt_indexes
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
    """Applied migrations must never be edited; changes belong in a new revision.

    This is a cheap guard against rewriting history: each revision's file must
    still declare its own id and must not refer to the revision that came later.
    """
    versions = PROJECT_ROOT / "migrations" / "versions"
    earlier = (versions / "0002_create_users_and_token_blocklist.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0002_create_users_and_token_blocklist"' in earlier
    assert "0003" not in earlier

    profiles = (versions / "0003_add_profiles_and_social.py").read_text(encoding="utf-8")
    assert 'revision: str = "0003_add_profiles_and_social"' in profiles
    assert "0004" not in profiles

    conversations = (versions / "0004_add_conversations_and_messages.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0004_add_conversations"' in conversations
    assert "0005" not in conversations

    client_key = (versions / "0005_add_message_client_id.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0005_message_client_id"' in client_key
    assert "0006" not in client_key

    statuses = (versions / "0006_add_message_statuses.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0006_message_statuses"' in statuses
    assert "0007" not in statuses

    media = (versions / "0007_add_media.py").read_text(encoding="utf-8")
    assert 'revision: str = "0007_add_media"' in media
    assert "0008" not in media

    groups = (versions / "0008_add_groups.py").read_text(encoding="utf-8")
    assert 'revision: str = "0008_add_groups"' in groups
    assert 'down_revision: str | None = "0007_add_media"' in groups
    assert "0009" not in groups


def test_email_uniqueness_is_enforced_by_the_database(migration_url: str) -> None:
    """Normalisation is applied in the app; the database is the hard guarantee."""
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        insert = text(
            "INSERT INTO users (email, username, hashed_password, is_active) "
            "VALUES (:email, :username, :hashed_password, TRUE)"
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
            "VALUES (:email, :username, :hashed_password, TRUE)"
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
                    "VALUES ('solo@example.com', 'solo', 'h', TRUE)"
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


def test_downgrade_two_steps_removes_groups_and_media(
    migration_url: str,
) -> None:
    """Rolling back 0008 and 0007 removes groups/media and restores text-only bodies."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "-2")

    engine = create_db_engine(migration_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"conversations", "conversation_members", "messages"} <= tables
        assert "media" not in tables
        assert "group_invitations" not in tables
        assert "message_receipts" not in tables

        conversation_columns = {
            column["name"] for column in inspector.get_columns("conversations")
        }
        assert "kind" not in conversation_columns
        assert "owner_id" not in conversation_columns
        member_columns = {
            column["name"] for column in inspector.get_columns("conversation_members")
        }
        assert "role" not in member_columns

        message_columns = {
            column["name"]: column for column in inspector.get_columns("messages")
        }
        assert message_columns["body"]["nullable"] is False
        assert {"delivered_at", "read_at", "client_message_id"} <= set(
            message_columns
        )

        # The downgrade must preserve the foreign keys and indexes, not just the
        # columns. This is the regression that a table rebuild would silently
        # lose.
        foreign_keys = {
            constraint["name"]
            for constraint in inspector.get_foreign_keys("messages")
        }
        assert {
            "fk_messages_conversation_id_conversations",
            "fk_messages_sender_id_users",
        } <= foreign_keys
        indexes = {index["name"] for index in inspector.get_indexes("messages")}
        assert {
            "ix_messages_conversation_id",
            "ix_messages_conversation_id_id",
            "ix_messages_sender_id",
            "uq_messages_conversation_sender_client",
        } <= indexes
    finally:
        engine.dispose()


def test_downgrade_three_steps_removes_the_status_columns(migration_url: str) -> None:
    """Rolling back 0008-0006 keeps every message and the idempotency key."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "-3")

    engine = create_db_engine(migration_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"conversations", "conversation_members", "messages"} <= tables
        assert "media" not in tables
        assert "group_invitations" not in tables
        assert "message_receipts" not in tables

        message_columns = {column["name"] for column in inspector.get_columns("messages")}
        assert "delivered_at" not in message_columns
        assert "read_at" not in message_columns
        assert "client_message_id" in message_columns
    finally:
        engine.dispose()


def test_downgrade_four_steps_removes_the_idempotency_key(
    migration_url: str,
) -> None:
    """Rolling back 0008-0005 still keeps every chat table and row."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "-4")

    engine = create_db_engine(migration_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"conversations", "conversation_members", "messages"} <= tables
        assert "media" not in tables
        assert "group_invitations" not in tables
        assert "message_receipts" not in tables

        message_columns = {column["name"] for column in inspector.get_columns("messages")}
        assert "client_message_id" not in message_columns
        assert "delivered_at" not in message_columns
        assert "read_at" not in message_columns
    finally:
        engine.dispose()


def test_downgrade_five_steps_removes_only_the_chat_tables(
    migration_url: str,
) -> None:
    """Rolling back 0008-0004 keeps the phase-6 profile schema."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "-5")

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
        } <= tables
        assert not {
            "conversations",
            "conversation_members",
            "messages",
            "media",
        } & tables

        # The profile columns from 0003 are untouched.
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        assert {"username", "token_version", "profile_visibility"} <= user_columns
    finally:
        engine.dispose()


def test_downgrade_six_steps_removes_the_profile_schema(migration_url: str) -> None:
    """0003's downgrade still works now that 0004-0008 sit on top of it."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "-6")

    engine = create_db_engine(migration_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"health_checks", "users", "token_blocklist"} <= tables
        assert not {
            "user_sessions",
            "verification_tokens",
            "contacts",
            "blocks",
            "conversations",
            "conversation_members",
            "messages",
            "media",
        } & tables

        user_columns = {column["name"] for column in inspector.get_columns("users")}
        assert "username" not in user_columns
        assert "token_version" not in user_columns
    finally:
        engine.dispose()


def test_status_migration_preserves_existing_messages(migration_url: str) -> None:
    """0006 only adds nullable columns, and 0007 keeps every message row."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "0005_message_client_id")

    engine = create_db_engine(migration_url)
    try:
        with engine.begin() as connection:
            for email, username in (
                ("alice@example.com", "alice"),
                ("bob@example.com", "bob"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO users (email, username, hashed_password, is_active) "
                        "VALUES (:email, :username, 'not-a-real-hash', TRUE)"
                    ),
                    {"email": email, "username": username},
                )
            connection.execute(text("INSERT INTO conversations (pair_key) VALUES ('1:2')"))
            connection.execute(
                text(
                    "INSERT INTO messages "
                    "(conversation_id, sender_id, body, client_message_id) "
                    "VALUES (1, 1, 'before-statuses', 'legacy-key')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_db_engine(migration_url)
    try:
        inspector = inspect(engine)
        body_column = next(
            column
            for column in inspector.get_columns("messages")
            if column["name"] == "body"
        )
        assert body_column["nullable"] is True
        assert "media" in inspector.get_table_names()
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT id, body, client_message_id, delivered_at, read_at "
                    "FROM messages"
                )
            ).one()
        assert row.id == 1
        assert row.body == "before-statuses"
        assert row.client_message_id == "legacy-key"
        assert row.delivered_at is None
        assert row.read_at is None
    finally:
        engine.dispose()

    command.downgrade(config, "-3")

    engine = create_db_engine(migration_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, body, client_message_id FROM messages")
            ).one()
        assert row.id == 1
        assert row.body == "before-statuses"
        assert row.client_message_id == "legacy-key"
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


def test_chat_constraints_are_enforced_by_the_database(migration_url: str) -> None:
    """The chat rules must hold even when application code is bypassed."""
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.begin() as connection:
            for email, username in (("alice@example.com", "alice"), ("bob@example.com", "bob")):
                connection.execute(
                    text(
                        "INSERT INTO users (email, username, hashed_password, is_active) "
                        "VALUES (:email, :username, 'not-a-real-hash', TRUE)"
                    ),
                    {"email": email, "username": username},
                )
            connection.execute(text("INSERT INTO conversations (pair_key) VALUES ('1:2')"))
            connection.execute(
                text("INSERT INTO conversation_members (conversation_id, user_id) VALUES (1, 1)")
            )
            connection.execute(
                text(
                    "INSERT INTO messages (conversation_id, sender_id, body) "
                    "VALUES (1, 1, 'hello')"
                )
            )

        # One conversation per pair, guaranteed by the unique index.
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO conversations (pair_key) VALUES ('1:2')"))

        # A member cannot be added to the same conversation twice.
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO conversation_members (conversation_id, user_id) "
                        "VALUES (1, 1)"
                    )
                )

        # A client retry cannot duplicate a message that sends the same key.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO messages "
                    "(conversation_id, sender_id, body, client_message_id) "
                    "VALUES (1, 1, 'retry', 'key-1')"
                )
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO messages "
                        "(conversation_id, sender_id, body, client_message_id) "
                        "VALUES (1, 1, 'retry', 'key-1')"
                    )
                )

        # Rows without a key are never constrained (NULLs are distinct).
        with engine.begin() as connection:
            for _ in range(2):
                connection.execute(
                    text(
                        "INSERT INTO messages (conversation_id, sender_id, body) "
                        "VALUES (1, 1, 'plain')"
                    )
                )

        # Blank bodies hit the CHECK constraint; oversized bodies are rejected
        # by PostgreSQL's VARCHAR(4000) length before the CHECK is evaluated.
        for body in ("   ", "x" * 4001):
            with pytest.raises((IntegrityError, DataError)):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO messages (conversation_id, sender_id, body) "
                            "VALUES (1, 1, :body)"
                        ),
                        {"body": body},
                    )
    finally:
        engine.dispose()


def test_media_constraints_are_enforced_by_the_database(migration_url: str) -> None:
    """Media rules hold even if a caller bypasses the service layer."""
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.begin() as connection:
            for email, username in (
                ("alice@example.com", "alice"),
                ("bob@example.com", "bob"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO users (email, username, hashed_password, is_active) "
                        "VALUES (:email, :username, 'not-a-real-hash', TRUE)"
                    ),
                    {"email": email, "username": username},
                )
            connection.execute(text("INSERT INTO conversations (pair_key) VALUES ('1:2')"))
            connection.execute(
                text(
                    "INSERT INTO messages (conversation_id, sender_id, body) "
                    "VALUES (1, 1, 'with media')"
                )
            )

        insert = text(
            "INSERT INTO media "
            "(public_id, storage_key, original_filename, content_type, size_bytes, "
            " width, height, status, conversation_id, uploader_id, message_id) "
            "VALUES (:public_id, :storage_key, 'photo.png', 'image/png', :size, "
            "        :width, :height, :status, 1, 1, :message_id)"
        )
        valid = {
            "public_id": "a" * 32,
            "storage_key": "b" * 32 + ".png",
            "size": 10,
            "width": 1,
            "height": 1,
            "status": "ready",
            "message_id": 1,
        }
        with engine.begin() as connection:
            connection.execute(insert, valid)

        # One attachment per message, enforced by a unique constraint.
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    insert,
                    {
                        **valid,
                        "public_id": "c" * 32,
                        "storage_key": "d" * 32 + ".png",
                    },
                )

        # Zero/negative size, half-specified dimensions and unknown statuses are
        # all refused structurally.
        for overrides in (
            {"size": 0, "message_id": None},
            {"width": 1, "height": None, "message_id": None},
            {"status": "processing", "message_id": None},
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        insert,
                        {
                            **valid,
                            "public_id": "e" * 32,
                            "storage_key": "f" * 32 + ".png",
                            **overrides,
                        },
                    )
    finally:
        engine.dispose()


def test_downgrade_refuses_to_drop_media_only_messages(migration_url: str) -> None:
    """The 0007 downgrade must not silently delete or rewrite message content."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (email, username, hashed_password, is_active) "
                    "VALUES ('alice@example.com', 'alice', 'h', TRUE)"
                )
            )
            connection.execute(text("INSERT INTO conversations (pair_key) VALUES ('1:1')"))
            connection.execute(
                text(
                    "INSERT INTO messages (conversation_id, sender_id, body) "
                    "VALUES (1, 1, NULL)"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="media-only messages"):
        command.downgrade(config, "-2")


def test_group_schema_and_constraints_are_enforced_by_the_database(
    migration_url: str,
) -> None:
    """Group membership, invitation and receipt invariants hold in SQL."""
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_db_engine(migration_url)
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
                    "VALUES ('group:test', 'group', 'Test Group', 'private', 1)"
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

        # Only one pending invitation per (group, invitee).
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(invitation_insert, {"status": "pending"})

        # After a decline/revoke, a new pending invitation is allowed.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE group_invitations SET status = 'declined', "
                    "responded_at = CURRENT_TIMESTAMP WHERE id = 1"
                )
            )
            connection.execute(invitation_insert, {"status": "pending"})

        # The status is a closed set and self-invitations are refused.
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

        # One receipt per message and member.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO messages (conversation_id, sender_id, body) "
                    "VALUES (1, 1, 'group message')"
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


def test_group_upgrade_preserves_existing_direct_rows(migration_url: str) -> None:
    """0008 backfills direct rows and leaves existing messages/members intact."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "0007_add_media")

    engine = create_db_engine(migration_url)
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
                text("INSERT INTO conversations (pair_key) VALUES ('1:2')")
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_members (conversation_id, user_id) "
                    "VALUES (1, 1), (1, 2)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO messages (conversation_id, sender_id, body) "
                    "VALUES (1, 1, 'before groups')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.connect() as connection:
            conversation = connection.execute(
                text(
                    "SELECT pair_key, kind, name, description, visibility, owner_id "
                    "FROM conversations WHERE id = 1"
                )
            ).one()
            roles = connection.execute(
                text(
                    "SELECT role FROM conversation_members "
                    "WHERE conversation_id = 1 ORDER BY user_id"
                )
            ).scalars().all()
            message = connection.execute(
                text("SELECT body FROM messages WHERE id = 1")
            ).scalar_one()
            group_tables = connection.execute(
                text(
                    "SELECT COUNT(*) FROM group_invitations"
                )
            ).scalar_one()
        assert conversation.pair_key == "1:2"
        assert conversation.kind == "direct"
        assert conversation.name is None
        assert conversation.description is None
        assert conversation.visibility is None
        assert conversation.owner_id is None
        assert roles == ["member", "member"]
        assert message == "before groups"
        assert group_tables == 0
    finally:
        engine.dispose()


def test_downgrade_refuses_to_delete_existing_groups(migration_url: str) -> None:
    """0008 must not silently discard group data on rollback."""
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")

    engine = create_db_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (email, username, hashed_password, is_active) "
                    "VALUES ('alice@example.com', 'alice', 'h', TRUE)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversations "
                    "(pair_key, kind, name, visibility, owner_id) "
                    "VALUES ('group:keep', 'group', 'Keep Me', 'private', 1)"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="group conversations"):
        command.downgrade(config, "-1")

    engine = create_db_engine(migration_url)
    try:
        with engine.connect() as connection:
            group = connection.execute(
                text(
                    "SELECT name, kind FROM conversations "
                    "WHERE pair_key = 'group:keep'"
                )
            ).one()
        assert group.name == "Keep Me"
        assert group.kind == "group"
    finally:
        engine.dispose()
