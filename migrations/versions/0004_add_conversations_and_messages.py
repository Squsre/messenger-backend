"""add conversations and messages

Revision ID: 0004_add_conversations
Revises: 0003_add_profiles_and_social
Create Date: 2026-10-20

Adds the one-to-one chat storage:

* ``conversations``       — one row per pair of participants. ``pair_key`` is the
  canonical ``"<lower-id>:<higher-id>"`` label and carries a **unique** index, so
  a duplicate pair cannot exist even under a concurrent create,
* ``conversation_members`` — one row per participant, unique per conversation,
* ``messages``            — append-only text messages with a structural CHECK on
  the body and a ``(conversation_id, id)`` index for chronological paging.

No existing table is altered, so the migration is safe on a populated database:
it only creates new tables.

Rollback note: downgrading drops the three chat tables **with their data**. It is
exercised by the test suite on a throw-away database; do not run it on a
production database without a backup.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_conversations"
down_revision: str | None = "0003_add_profiles_and_social"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors app.core.security.MESSAGE_MAX_LENGTH. Inlined so the migration stays
# reproducible even if the application policy changes later.
_MESSAGE_MAX_LENGTH = 4000


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pair_key", sa.String(length=64), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(op.f("ix_conversations_pair_key"), "conversations", ["pair_key"], unique=True)

    op.create_table(
        "conversation_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_members_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversation_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_members")),
        sa.UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_conversation_members_conversation_user",
        ),
    )
    op.create_index(
        op.f("ix_conversation_members_conversation_id"),
        "conversation_members",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_members_user_id"),
        "conversation_members",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.String(length=_MESSAGE_MAX_LENGTH), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(body)) > 0",
            name=op.f("ck_messages_body_not_blank"),
        ),
        sa.CheckConstraint(
            f"length(body) <= {_MESSAGE_MAX_LENGTH}",
            name=op.f("ck_messages_body_too_long"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["users.id"],
            name=op.f("fk_messages_sender_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"),
        "messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_messages_sender_id"), "messages", ["sender_id"], unique=False)
    op.create_index(
        "ix_messages_conversation_id_id",
        "messages",
        ["conversation_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id_id", table_name="messages")
    op.drop_index(op.f("ix_messages_sender_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(
        op.f("ix_conversation_members_user_id"), table_name="conversation_members"
    )
    op.drop_index(
        op.f("ix_conversation_members_conversation_id"),
        table_name="conversation_members",
    )
    op.drop_table("conversation_members")

    op.drop_index(op.f("ix_conversations_pair_key"), table_name="conversations")
    op.drop_table("conversations")
