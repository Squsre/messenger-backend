"""Conversation membership and group roles.

One row per participant. The pair ``(conversation_id, user_id)`` is unique, so
the same account can never be joined to a conversation twice. Authorization
everywhere else in the chat layer is derived from this table: no row means no
access, and the API reports a conversation the caller does not belong to as
``404`` so the id cannot be used to probe for existence.

Direct conversations keep the default ``member`` role. Group conversations use
``owner``, ``admin`` and ``member``; the role is always verified against the
database on the server and is never taken from a request body.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GroupRole(StrEnum):
    """Roles a member may hold in a group conversation."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class ConversationMember(Base):
    """A user participating in a conversation."""

    __tablename__ = "conversation_members"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_conversation_members_conversation_user",
        ),
        # The member list and role checks both filter by conversation + role.
        Index(
            "ix_conversation_members_conversation_id_role",
            "conversation_id",
            "role",
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
    #: ``owner``/``admin``/``member``. Direct chats always use ``member``.
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GroupRole.MEMBER.value,
        server_default=GroupRole.MEMBER.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<ConversationMember conversation={self.conversation_id!r} "
            f"user={self.user_id!r} role={self.role!r}>"
        )
