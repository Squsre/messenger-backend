"""Private-group invitations.

An invitation is a small state machine scoped to one group and one invitee:
``pending`` -> ``accepted`` / ``declined`` / ``revoked``. Only a pending
invitation can be accepted, declined or revoked. A partial unique index keeps at
most one pending invitation per ``(group, invitee)`` pair while still allowing a
new invitation after a previous one was declined or revoked.

The inviter and invitee are always resolved from the database by the service;
the request body only names a target, never a role or an authorisation claim.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InvitationStatus(StrEnum):
    """Lifecycle of a group invitation."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REVOKED = "revoked"


_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in InvitationStatus)
_STATUS_SQL_LIST = ", ".join(f"'{value}'" for value in _STATUS_VALUES)


class GroupInvitation(Base):
    """One invitation to join one private/public group."""

    __tablename__ = "group_invitations"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_STATUS_SQL_LIST})",
            name="status_allowed",
        ),
        CheckConstraint("inviter_id <> invitee_id", name="no_self_invite"),
        Index(
            "ix_group_invitations_conversation_status",
            "conversation_id",
            "status",
        ),
        Index("ix_group_invitations_invitee_status", "invitee_id", "status"),
        Index("ix_group_invitations_inviter_id", "inviter_id"),
        # At most one *pending* invitation per group and invitee. PostgreSQL
        # partial indexes enforce this; a plain unique constraint would
        # incorrectly prevent re-inviting after a decline/revoke.
        Index(
            "uq_group_invitations_pending",
            "conversation_id",
            "invitee_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    inviter_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    invitee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=InvitationStatus.PENDING.value,
        server_default=InvitationStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<GroupInvitation id={self.id!r} conversation={self.conversation_id!r} "
            f"invitee={self.invitee_id!r} status={self.status!r}>"
        )
