"""Conversation messages.

Messages are append-only in this phase: there is no edit or delete endpoint, so
the table has ``created_at`` only. Ordering by the monotonic primary key is both
chronological and stable (two rows can share a timestamp, but never an id),
which is what the history endpoint pages over.

The body is validated twice on purpose: by the request schema (a friendly
``422``) and by CHECK constraints here, so an empty or oversized row cannot be
written even if application code is bypassed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import MESSAGE_MAX_LENGTH
from app.db.base import Base


class Message(Base):
    """A single text message inside a conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("length(trim(body)) > 0", name="body_not_blank"),
        CheckConstraint(f"length(body) <= {MESSAGE_MAX_LENGTH}", name="body_too_long"),
        # The history query is always "this conversation, in id order".
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The sender is always an authenticated participant; the service checks it.
    sender_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(String(MESSAGE_MAX_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<Message id={self.id!r} conversation={self.conversation_id!r} "
            f"sender={self.sender_id!r}>"
        )
