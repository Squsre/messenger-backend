"""Chat request/response schemas.

Two things are deliberate about the response shapes:

* a message exposes the **sender's username**, never a numeric user id — the rest
  of the API keeps numeric ids private (see ``app.schemas.user``),
* a conversation exposes the *other* participant as a ``UserPublic``, so the same
  privacy rules (hidden last-seen, no email) apply here as everywhere else.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import (
    MESSAGE_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
)
from app.db.models import Message
from app.schemas.user import UserPublic


class ConversationCreate(BaseModel):
    """Start (or reopen) a private conversation with another account."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        description="Username of the account to talk to.",
        examples=["bob"],
    )


class MessageCreate(BaseModel):
    """Send a text message.

    The body is trimmed before it is stored, so a message never begins or ends
    with whitespace, and a whitespace-only body is rejected. ``extra="forbid"``
    means a client cannot smuggle in ``sender_id``, ``conversation_id`` or
    ``created_at``.
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(
        min_length=1,
        max_length=MESSAGE_MAX_LENGTH,
        description="Message text (plain text, 1-4000 characters after trimming).",
        examples=["Hey, are we still on for tomorrow?"],
    )

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Message body must not be empty.")
        return text


class MessageRead(BaseModel):
    """A stored message, including the recipient's status timestamps."""

    id: int
    conversation_id: int
    sender_username: str = Field(description="Who sent the message.")
    body: str
    created_at: datetime
    delivered_at: datetime | None = Field(
        default=None,
        description=(
            "When the other participant's device received the message; null "
            "until it was pushed to a live socket or marked read."
        ),
    )
    read_at: datetime | None = Field(
        default=None,
        description=(
            "When the other participant read the message; null until they "
            "explicitly marked it read. Reading always implies delivery."
        ),
    )


class MarkReadRequest(BaseModel):
    """Advance the read watermark of a conversation."""

    model_config = ConfigDict(extra="forbid")

    last_message_id: int = Field(
        ge=1,
        description=(
            "Mark every message from the other participant with an id up to and "
            "including this one as read."
        ),
        examples=[42],
    )


class ReadReceipt(BaseModel):
    """Outcome of a read-watermark update."""

    conversation_id: int
    last_read_message_id: int
    read_at: datetime | None = Field(
        default=None,
        description="When the update happened; null when everything was already read.",
    )
    updated: int = Field(description="Number of messages that changed.")
    unread_count: int = Field(description="Unread messages left in the conversation.")


class ConversationRead(BaseModel):
    """A conversation as it appears in the caller's list."""

    id: int
    participant: UserPublic = Field(description="The other participant.")
    last_message: MessageRead | None = Field(
        default=None,
        description="Newest message, or null for a conversation with no messages yet.",
    )
    created_at: datetime
    last_message_at: datetime | None = Field(
        default=None,
        description="Newest activity; null until the first message is sent.",
    )
    unread_count: int = Field(
        default=0,
        description="Messages from the other participant that the caller has not read.",
    )


class ConversationDetail(ConversationRead):
    """A single conversation, with the total number of messages."""

    message_count: int = Field(description="Number of messages in the conversation.")


class ConversationPage(BaseModel):
    """A page of conversations plus paging metadata."""

    items: list[ConversationRead]
    total: int = Field(description="Total conversations the caller participates in.")
    limit: int = Field(description="Maximum items requested.")
    offset: int = Field(description="Number of items skipped.")


class MessagePage(BaseModel):
    """A chronological page of messages plus paging metadata."""

    items: list[MessageRead] = Field(description="Oldest first.")
    total: int = Field(description="Total messages in the conversation.")
    limit: int = Field(description="Maximum items requested.")
    offset: int = Field(description="Number of items skipped.")


def build_message(message: Message, *, sender_username: str) -> MessageRead:
    """Convert a stored message to its API representation."""
    return MessageRead(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_username=sender_username,
        body=message.body,
        created_at=message.created_at,
        delivered_at=message.delivered_at,
        read_at=message.read_at,
    )
