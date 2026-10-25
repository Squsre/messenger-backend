"""Conversation containers for private chats and groups.

A conversation is only the container; the participants live in
:class:`~app.db.models.conversation_member` and the content in
:class:`~app.db.models.message`.

Direct conversations
--------------------
``pair_key`` is the canonical ``"<lower-id>:<higher-id>"`` label of the two
participants. The unique index on it is what makes "one conversation per pair"
a **database** guarantee: two concurrent requests cannot create a duplicate,
even if the application-level lookup races. Keeping it a plain string (instead
of a composite unique index over the member table) also means the rule is
expressible on SQLite and PostgreSQL identically.

Group conversations
-------------------
Groups reuse the same message and membership tables, so there is only one chat
pipeline and one message model. A group row has ``kind='group'``, a name, a
visibility and an owner. ``pair_key`` remains non-null (and unique) for groups,
but it is a server-generated synthetic value with a ``group:`` prefix. It is
never used for lookup and cannot collide with the numeric direct-chat key. This
keeps the existing SQLite schema safe to extend without rebuilding the parent
table (which would be unsafe while child foreign keys exist).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ConversationKind(StrEnum):
    """The two kinds of conversation supported by this phase."""

    DIRECT = "direct"
    GROUP = "group"


class GroupVisibility(StrEnum):
    """Who may discover and join a group."""

    PRIVATE = "private"
    PUBLIC = "public"


class Conversation(TimestampMixin, Base):
    """A direct conversation or a group."""

    __tablename__ = "conversations"
    __table_args__ = (
        # Public group discovery and the user's group list both filter on these.
        Index("ix_conversations_kind_visibility", "kind", "visibility"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: Direct: ``"<min(participant ids)>:<max(participant ids)>"``.
    #: Group: a random ``"group:<hex>"`` value; never used for lookup.
    pair_key: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    #: ``direct`` or ``group``; direct rows are the default for old databases.
    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ConversationKind.DIRECT.value,
        server_default=ConversationKind.DIRECT.value,
    )
    #: Group display name; null for direct conversations.
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Optional group description; null for direct conversations.
    description: Mapped[str | None] = mapped_column(String(280), nullable=True)
    #: ``private``/``public`` for groups; null for direct conversations.
    visibility: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Group owner. The owner also has a ``conversation_members`` row with the
    #: ``owner`` role, which is the authorization source of truth. This column is
    #: a denormalised reference for display and list queries; the service keeps
    #: it in sync on creation and ownership transfer.
    owner_id: Mapped[int | None] = mapped_column(
        nullable=True,
        index=True,
    )
    #: Denormalised for the conversation list: newest activity first. ``NULL``
    #: until the first message is sent.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<Conversation id={self.id!r} kind={self.kind!r} "
            f"pair={self.pair_key!r}>"
        )
