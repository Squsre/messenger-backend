"""add profiles, account security and social tables

Revision ID: 0003_add_profiles_and_social
Revises: 0002_create_users_and_token_blocklist
Create Date: 2026-10-18

Adds, without touching any existing data:

* profile + account-security columns on ``users`` (``username``,
  ``display_name``, ``bio``, ``avatar_key``, ``profile_visibility``,
  ``show_last_seen``, ``last_seen_at``, ``email_verified_at``,
  ``token_version``),
* ``user_sessions``      — logged-in devices (id == access-token ``jti``),
* ``verification_tokens`` — hashed, single-use email-verification/reset tokens,
* ``contacts``           — one-directional contact relationships,
* ``blocks``             — user-to-user blocks.

``users.username`` is added nullable, backfilled deterministically for accounts
created before usernames existed, and only then made ``NOT NULL`` with a unique
index. The backfill derives a handle from the email local part, sanitises it to
the documented character set and de-duplicates with a numeric suffix in ``id``
order, so it is deterministic: running the migration twice produces the same
names, and no account is ever given a duplicate or reserved handle.

Rollback note: downgrading drops the profile/security columns, so profile data,
verification state and device sessions are **lost** if you downgrade after
users have filled them in. The schema-level downgrade is exercised in
``tests/test_migrations.py``; do not run it on a database with real data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_profiles_and_social"
down_revision: str | None = "0002_create_users_and_token_blocklist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USERNAME_MAX_LENGTH = 32
_USERNAME_MIN_LENGTH = 3

# Mirrors app.core.security.RESERVED_USERNAMES. Inlined on purpose: a migration
# must stay reproducible even if the application's reserved list changes later.
_RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "administrator",
        "anonymous",
        "api",
        "auth",
        "block",
        "blocks",
        "contact",
        "contacts",
        "docs",
        "help",
        "login",
        "logout",
        "me",
        "moderator",
        "openapi",
        "redoc",
        "register",
        "root",
        "search",
        "security",
        "settings",
        "signup",
        "support",
        "system",
        "user",
        "users",
    }
)

_ILLEGAL_USERNAME_CHARS = re.compile(r"[^a-z0-9_]+")
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")

# New profile/security columns, in the order they are added.
_USER_COLUMNS = (
    sa.Column("username", sa.String(length=32), nullable=True),
    sa.Column("display_name", sa.String(length=64), nullable=True),
    sa.Column("bio", sa.String(length=280), nullable=True),
    sa.Column("avatar_key", sa.String(length=255), nullable=True),
    sa.Column(
        "profile_visibility",
        sa.String(length=16),
        nullable=False,
        server_default="public",
    ),
    sa.Column("show_last_seen", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
)


def _derive_username(email: str | None) -> str:
    """Derive the canonical username for an account created before this phase.

    The logic is a deliberate copy of
    :func:`app.services.user.derive_username`; see the module docstring.
    """
    local_part = (email or "").strip().lower().split("@", 1)[0]
    base = _ILLEGAL_USERNAME_CHARS.sub("_", local_part)
    base = _REPEATED_UNDERSCORES.sub("_", base).strip("_")

    if len(base) < _USERNAME_MIN_LENGTH:
        base = f"{base}user"[:_USERNAME_MAX_LENGTH]
    base = base[:_USERNAME_MAX_LENGTH]

    if base in _RESERVED_USERNAMES:
        base = f"{base}_1"[:_USERNAME_MAX_LENGTH]
    return base


def _backfill_usernames() -> None:
    """Give every pre-existing account a unique, deterministic username."""
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, username, email FROM users ORDER BY id")
    ).fetchall()

    taken: set[str] = {row.username for row in rows if row.username}

    for row in rows:
        if row.username:
            continue

        base = _derive_username(row.email)
        candidate = base
        suffix = 2
        while candidate in taken:
            tail = f"_{suffix}"
            candidate = f"{base[: _USERNAME_MAX_LENGTH - len(tail)]}{tail}"
            suffix += 1
        taken.add(candidate)
        connection.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :id"),
            {"username": candidate, "id": row.id},
        )


def upgrade() -> None:
    # 1) New columns, initially nullable so existing rows survive the change.
    with op.batch_alter_table("users") as batch_op:
        for column in _USER_COLUMNS:
            batch_op.add_column(column)

    # 2) Fill in a username for every account that predates the column.
    _backfill_usernames()

    # 3) Enforce the invariants now that the data satisfies them.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "username",
            existing_type=sa.String(length=32),
            nullable=False,
        )
        batch_op.create_check_constraint(
            op.f("ck_users_profile_visibility_valid"),
            "profile_visibility IN ('public', 'contacts', 'private')",
        )
        batch_op.create_check_constraint(
            op.f("ck_users_token_version_non_negative"),
            "token_version >= 0",
        )

    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    # 4) Device sessions. ``session_id`` is the access token's ``jti``; the token
    #    itself is never stored.
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
    )
    op.create_index(
        op.f("ix_user_sessions_session_id"), "user_sessions", ["session_id"], unique=True
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_user_sessions_expires_at"), "user_sessions", ["expires_at"], unique=False
    )

    # 5) Hashed single-use tokens for email verification and password reset.
    op.create_table(
        "verification_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_verification_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_tokens")),
    )
    op.create_index(
        op.f("ix_verification_tokens_token_hash"),
        "verification_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_verification_tokens_user_id"), "verification_tokens", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_verification_tokens_expires_at"),
        "verification_tokens",
        ["expires_at"],
        unique=False,
    )

    # 6) One-directional contacts.
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("owner_id <> contact_id", name=op.f("ck_contacts_contacts_no_self")),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["users.id"],
            name=op.f("fk_contacts_contact_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_contacts_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("owner_id", "contact_id", name="uq_contacts_owner_contact"),
    )
    op.create_index(op.f("ix_contacts_owner_id"), "contacts", ["owner_id"], unique=False)
    op.create_index(op.f("ix_contacts_contact_id"), "contacts", ["contact_id"], unique=False)
    op.create_index("ix_contacts_owner_created", "contacts", ["owner_id", "created_at"])

    # 7) Blocks.
    op.create_table(
        "blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("blocker_id", sa.Integer(), nullable=False),
        sa.Column("blocked_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("blocker_id <> blocked_id", name=op.f("ck_blocks_blocks_no_self")),
        sa.ForeignKeyConstraint(
            ["blocked_id"],
            ["users.id"],
            name=op.f("fk_blocks_blocked_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["blocker_id"],
            ["users.id"],
            name=op.f("fk_blocks_blocker_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blocks")),
        sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_blocks_blocker_blocked"),
    )
    op.create_index(op.f("ix_blocks_blocker_id"), "blocks", ["blocker_id"], unique=False)
    op.create_index(op.f("ix_blocks_blocked_id"), "blocks", ["blocked_id"], unique=False)
    op.create_index("ix_blocks_blocker_created", "blocks", ["blocker_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_blocks_blocker_created", table_name="blocks")
    op.drop_index(op.f("ix_blocks_blocked_id"), table_name="blocks")
    op.drop_index(op.f("ix_blocks_blocker_id"), table_name="blocks")
    op.drop_table("blocks")

    op.drop_index("ix_contacts_owner_created", table_name="contacts")
    op.drop_index(op.f("ix_contacts_contact_id"), table_name="contacts")
    op.drop_index(op.f("ix_contacts_owner_id"), table_name="contacts")
    op.drop_table("contacts")

    op.drop_index(op.f("ix_verification_tokens_expires_at"), table_name="verification_tokens")
    op.drop_index(op.f("ix_verification_tokens_user_id"), table_name="verification_tokens")
    op.drop_index(op.f("ix_verification_tokens_token_hash"), table_name="verification_tokens")
    op.drop_table("verification_tokens")

    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_session_id"), table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index(op.f("ix_users_username"), table_name="users")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint(op.f("ck_users_token_version_non_negative"), type_="check")
        batch_op.drop_constraint(op.f("ck_users_profile_visibility_valid"), type_="check")
        for column in reversed(_USER_COLUMNS):
            batch_op.drop_column(column.name)
