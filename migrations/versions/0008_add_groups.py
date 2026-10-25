"""add groups, memberships, invitations and per-member receipts

Revision ID: 0008_add_groups
Revises: 0007_add_media
Create Date: 2026-10-25

Groups reuse ``conversations`` and ``messages`` rather than introducing a
parallel message store. This migration extends the existing tables and adds the
two new tables the group layer needs:

* ``conversations.kind`` / ``name`` / ``description`` / ``visibility`` /
  ``owner_id``: group metadata. Existing rows become ``kind='direct'``.
* ``conversation_members.role``: owner/admin/member. Existing rows become
  ``member`` (direct chats have no privileged role).
* ``group_invitations``: pending/accepted/declined/revoked invitations, with a
  partial unique index preventing two pending invitations for the same invitee.
* ``message_receipts``: per-member delivery/read state for group messages. The
  existing message timestamps remain the source of truth for direct chats.

``pair_key`` deliberately stays non-null and unique. Group rows use a random
``group:<hex>`` value; this avoids a risky rebuild of the parent
``conversations`` table while child foreign keys exist on SQLite, and cannot
collide with the numeric direct-chat key.

Downgrade note: if any group conversations exist, the downgrade refuses rather
than silently deleting group messages and memberships. Remove the groups or
restore a backup first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_add_groups"
down_revision: str | None = "0007_add_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="direct",
        ),
    )
    op.add_column(
        "conversations", sa.Column("name", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("description", sa.String(length=280), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("visibility", sa.String(length=16), nullable=True),
    )
    # Owner is the member whose membership role is ``owner``. The denormalised
    # id is intentionally not a database FK: SQLite cannot add a *named* FK to an
    # existing table via ALTER, and a full table rebuild would be unsafe while
    # child foreign keys exist. The service enforces ownership consistency and
    # there is no account-deletion endpoint that could orphan it.
    op.add_column(
        "conversations",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_conversations_kind_visibility",
        "conversations",
        ["kind", "visibility"],
    )
    op.create_index(
        op.f("ix_conversations_owner_id"), "conversations", ["owner_id"]
    )

    op.add_column(
        "conversation_members",
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default="member",
        ),
    )
    op.create_index(
        "ix_conversation_members_conversation_id_role",
        "conversation_members",
        ["conversation_id", "role"],
    )

    op.create_table(
        "group_invitations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("inviter_id", sa.Integer(), nullable=False),
        sa.Column("invitee_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked')",
            name=op.f("ck_group_invitations_status_allowed"),
        ),
        sa.CheckConstraint(
            "inviter_id <> invitee_id",
            name=op.f("ck_group_invitations_no_self_invite"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_group_invitations_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"],
            ["users.id"],
            name=op.f("fk_group_invitations_inviter_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invitee_id"],
            ["users.id"],
            name=op.f("fk_group_invitations_invitee_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_invitations")),
    )
    op.create_index(
        "ix_group_invitations_conversation_status",
        "group_invitations",
        ["conversation_id", "status"],
    )
    op.create_index(
        "ix_group_invitations_invitee_status",
        "group_invitations",
        ["invitee_id", "status"],
    )
    op.create_index(
        "ix_group_invitations_inviter_id",
        "group_invitations",
        ["inviter_id"],
    )
    op.create_index(
        "uq_group_invitations_pending",
        "group_invitations",
        ["conversation_id", "invitee_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "message_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_receipts_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_message_receipts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_receipts")),
        sa.UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_message_receipts_message_user",
        ),
    )
    op.create_index(
        "ix_message_receipts_user_id_read_at",
        "message_receipts",
        ["user_id", "read_at"],
    )
    op.create_index(
        "ix_message_receipts_message_id_read_at",
        "message_receipts",
        ["message_id", "read_at"],
    )


def downgrade() -> None:
    group_count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM conversations WHERE kind = 'group'")
    ).scalar_one()
    if group_count:
        raise RuntimeError(
            "Cannot downgrade 0008 while group conversations exist; "
            "delete the groups or restore a backup first."
        )

    op.drop_table("message_receipts")
    op.drop_table("group_invitations")

    op.drop_index(
        "ix_conversation_members_conversation_id_role",
        table_name="conversation_members",
    )
    op.drop_column("conversation_members", "role")

    op.drop_index(op.f("ix_conversations_owner_id"), table_name="conversations")
    op.drop_index(
        "ix_conversations_kind_visibility", table_name="conversations"
    )
    op.drop_column("conversations", "owner_id")
    op.drop_column("conversations", "visibility")
    op.drop_column("conversations", "description")
    op.drop_column("conversations", "name")
    op.drop_column("conversations", "kind")
