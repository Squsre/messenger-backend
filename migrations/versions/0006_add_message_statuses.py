"""add delivered_at and read_at to messages

Revision ID: 0006_message_statuses
Revises: 0005_message_client_id
Create Date: 2026-10-24

Adds the recipient's persistent status timestamps for the one-to-one chat:

* ``messages.delivered_at`` — when the message reached one of the recipient's
  live devices,
* ``messages.read_at`` — when the recipient explicitly marked it read.

Both columns are nullable, so every existing message simply starts as "sent but
not delivered/read" and no row is rewritten. No other table or constraint is
touched.

Rollback note: downgrading drops the two timestamps (and therefore the recorded
delivery/read state) but keeps the messages themselves.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_message_statuses"
down_revision: str | None = "0005_message_client_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``ALTER TABLE ... ADD COLUMN`` preserves the existing CHECK constraints
    # and indexes without a rebuild.
    op.add_column(
        "messages",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "read_at")
    op.drop_column("messages", "delivered_at")
