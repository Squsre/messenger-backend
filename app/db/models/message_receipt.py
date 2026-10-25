"""Per-member delivery and read receipts for group messages.

Direct conversations can keep one ``delivered_at``/``read_at`` pair on the
message because there is exactly one recipient. A group has many recipients, so
a shared status would be wrong: one member reading a message must not mark it
read for everybody. This table stores one row per ``(message, member)``.

The direct columns on ``messages`` remain the source of truth for two-person
conversations. Group endpoints only consult this table, and the sender's own
receipt is not needed (the sender always knows their own message).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MessageReceipt(Base):
    """Delivery/read state of one group message for one member."""

    __tablename__ = "message_receipts"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_message_receipts_message_user",
        ),
        # The unread-count query filters by user and read state; the aggregate
        # query for one message filters by message and read state.
        Index("ix_message_receipts_user_id_read_at", "user_id", "read_at"),
        Index("ix_message_receipts_message_id_read_at", "message_id", "read_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<MessageReceipt message={self.message_id!r} user={self.user_id!r} "
            f"read={self.read_at!r}>"
        )
