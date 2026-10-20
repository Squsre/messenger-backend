"""Conversation membership.

One row per participant. The pair ``(conversation_id, user_id)`` is unique, so
the same account can never be joined to a conversation twice. Authorization
everywhere else in the chat layer is derived from this table: no row means no
access, and the API reports a conversation the caller does not belong to as
``404`` so the id cannot be used to probe for existence.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConversationMember(Base):
    """A user participating in a conversation."""

    __tablename__ = "conversation_members"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_conversation_members_conversation_user",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<ConversationMember conversation={self.conversation_id!r} user={self.user_id!r}>"
