"""add client_message_id to messages

Revision ID: 0005_message_client_id
Revises: 0004_add_conversations
Create Date: 2026-10-20

Adds the optional idempotency key used by the realtime client:

* ``messages.client_message_id`` (nullable, so existing rows and ordinary sends
  are unaffected),
* a unique index over ``(conversation_id, sender_id, client_message_id)`` so a
  retry after a dropped connection cannot create a duplicate. SQL treats
  ``NULL`` values as distinct in a unique index, so messages without a key are
  never constrained.

The migration adds a column and an index only; it rewrites no rows and does not
touch any other table.

Rollback note: downgrading drops the index and the column (and therefore the
recorded keys), not the messages themselves.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_message_client_id"
down_revision: str | None = "0004_add_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``ALTER TABLE ... ADD COLUMN`` and ``CREATE [UNIQUE] INDEX`` are both
    # supported by SQLite and PostgreSQL, so no table rebuild (batch mode) is
    # needed — which also keeps the existing CHECK constraints untouched.
    op.add_column(
        "messages",
        sa.Column("client_message_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_messages_conversation_sender_client",
        "messages",
        ["conversation_id", "sender_id", "client_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_messages_conversation_sender_client", table_name="messages")
    op.drop_column("messages", "client_message_id")
