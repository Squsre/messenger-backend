"""Conversation messages.

Messages are append-only in this phase: there is no edit or delete endpoint, so
the table has ``created_at`` plus the recipient's delivery/read timestamps.
Ordering by the monotonic primary key is both chronological and stable (two rows
can share a timestamp, but never an id), which is what the history endpoint
pages over.

``delivered_at`` and ``read_at`` describe the **other** participant in the
conversation (the app is strictly one-to-one), so they can live on the message
instead of a per-recipient table:

* ``delivered_at`` — the message was pushed to a live socket of the recipient
  (or implicitly when the recipient marks it read),
* ``read_at`` — the recipient explicitly marked it read; ``read_at`` implies
  ``delivered_at``.

Both are ``NULL`` until that happens and are only ever set once, so a client can
treat them as monotonic.

``client_message_id`` is an **optional** idempotency key supplied by a realtime
client. A partial-style unique index over ``(conversation_id, sender_id,
client_message_id)`` makes a retry after a dropped connection return the message
that was already stored instead of creating a second one. Rows without a key
store ``NULL``; both SQLite and PostgreSQL allow any number of ``NULL`` entries
in a unique index, so ordinary sends are unaffected.

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
        # A client retry (same sender, conversation and key) must not duplicate.
        Index(
            "uq_messages_conversation_sender_client",
            "conversation_id",
            "sender_id",
            "client_message_id",
            unique=True,
        ),
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
    #: Optional client-supplied idempotency key (see the module docstring).
    client_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    #: When the recipient's device received the message (see module docstring).
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    #: When the recipient read the message; always >= ``delivered_at``.
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<Message id={self.id!r} conversation={self.conversation_id!r} "
            f"sender={self.sender_id!r}>"
        )
