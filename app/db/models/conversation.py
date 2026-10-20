"""One-to-one private conversations.

A conversation is only the container; the participants live in
:class:`~app.db.models.conversation_member` and the content in
:class:`~app.db.models.message`.

``pair_key`` is the canonical ``"<lower-id>:<higher-id>"`` label of the two
participants. The unique index on it is what makes "one conversation per pair"
a **database** guarantee: two concurrent requests cannot create a duplicate,
even if the application-level lookup races. Keeping it a plain string (instead
of a composite unique index over the member table) also means the rule is
expressible on SQLite and PostgreSQL identically.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Conversation(TimestampMixin, Base):
    """A private conversation between exactly two accounts."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: ``"<min(participant ids)>:<max(participant ids)>"`` — see the module docstring.
    pair_key: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    #: Denormalised for the conversation list: newest activity first. ``NULL``
    #: until the first message is sent.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Conversation id={self.id!r} pair={self.pair_key!r}>"
