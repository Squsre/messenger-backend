"""add media storage and optional message bodies

Revision ID: 0007_add_media
Revises: 0006_message_statuses
Create Date: 2026-10-24

Adds conversation-scoped media metadata and makes ``messages.body`` nullable so
a message can carry text, media, or both. The binary is **not** stored in the
database; only the opaque storage key and safe metadata are.

PostgreSQL supports altering nullability and replacing CHECK constraints in
place, so this migration uses ordinary ``ALTER TABLE`` operations and preserves
every existing row.

Rollback note: downgrading drops the ``media`` table (and its metadata). If any
media-only message exists (``body IS NULL``), the downgrade refuses instead of
silently deleting messages or inventing placeholder text. Remove/attach text to
those messages first, or restore from a backup.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_add_media"
down_revision: str | None = "0006_message_statuses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MESSAGE_MAX_LENGTH = 4000


def upgrade() -> None:
    # ``op.f`` marks these as final names. Without it Alembic applies the
    # ``ck_%(table_name)s_%(constraint_name)s`` naming convention a second
    # time and PostgreSQL looks for ``ck_messages_ck_messages_...``.
    op.drop_constraint(
        op.f("ck_messages_body_not_blank"), "messages", type_="check"
    )
    op.drop_constraint(
        op.f("ck_messages_body_too_long"), "messages", type_="check"
    )
    op.alter_column(
        "messages",
        "body",
        existing_type=sa.String(length=_MESSAGE_MAX_LENGTH),
        nullable=True,
    )
    op.create_check_constraint(
        op.f("ck_messages_body_not_blank"),
        "messages",
        "body IS NULL OR length(trim(body)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_messages_body_too_long"),
        "messages",
        f"body IS NULL OR length(body) <= {_MESSAGE_MAX_LENGTH}",
    )

    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=128), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="ready",
            nullable=False,
        ),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("uploader_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_media_size_positive")),
        sa.CheckConstraint(
            "(width IS NULL AND height IS NULL) OR "
            "(width IS NOT NULL AND height IS NOT NULL AND width > 0 AND height > 0)",
            name=op.f("ck_media_dimensions_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')",
            name=op.f("ck_media_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_media_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploader_id"],
            ["users.id"],
            name=op.f("fk_media_uploader_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_media_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media")),
        sa.UniqueConstraint("public_id", name="uq_media_public_id"),
        sa.UniqueConstraint("storage_key", name="uq_media_storage_key"),
        sa.UniqueConstraint("message_id", name="uq_media_message_id"),
    )
    op.create_index(
        "ix_media_conversation_id_created_at",
        "media",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        op.f("ix_media_uploader_id"), "media", ["uploader_id"], unique=False
    )


def downgrade() -> None:
    # Check before dropping anything: a refusal must leave both the media table
    # and the message rows untouched, even on a backend with non-transactional
    # DDL.
    null_bodies = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM messages WHERE body IS NULL")
    ).scalar_one()
    if null_bodies:
        raise RuntimeError(
            "Cannot downgrade 0007 while media-only messages exist; "
            "add text to them or restore a backup."
        )

    op.drop_index(op.f("ix_media_uploader_id"), table_name="media")
    op.drop_index(
        "ix_media_conversation_id_created_at", table_name="media"
    )
    op.drop_table("media")

    op.drop_constraint(
        op.f("ck_messages_body_not_blank"), "messages", type_="check"
    )
    op.drop_constraint(
        op.f("ck_messages_body_too_long"), "messages", type_="check"
    )
    op.alter_column(
        "messages",
        "body",
        existing_type=sa.String(length=_MESSAGE_MAX_LENGTH),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_messages_body_not_blank"),
        "messages",
        "length(trim(body)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_messages_body_too_long"),
        "messages",
        f"length(body) <= {_MESSAGE_MAX_LENGTH}",
    )
