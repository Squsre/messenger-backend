"""WebSocket frame schemas.

Every frame is a single JSON object with a ``type`` discriminator. Inbound and
outbound models are declared explicitly (and forbidding unknown fields) so the
contract is documented and a client cannot smuggle in ``sender_id``,
``created_at`` or similar server-owned data.

Inbound
-------
``{"type": "auth", "token": "..."}``
    Must be the **first** frame. The token is validated with the same four
    layers as the HTTP API. It never appears in the URL or in a log line.

``{"type": "message", "conversation_id": 1, "body": "hi", "media_id": "...", "client_message_id": "..."}``
    Send a message with text, media, or both. ``client_message_id`` is optional
    and makes retries safe.

``{"type": "read", "conversation_id": 1, "last_message_id": 42}``
    Mark incoming messages up to (and including) ``last_message_id`` as read.
    Confirmed (and fanned out to the other participant) with a ``read`` event.

``{"type": "typing", "conversation_id": 1, "is_typing": true}``
    Ephemeral typing state. It is **not persisted**: the server validates
    membership and forwards it to the other participant's live connections.

``{"type": "ping"}``
    Liveness check; answered with ``pong``.

``{"type": "pong"}``
    Answer to a server ``ping``. It only refreshes the idle timer; it is not
    stored or acknowledged.

Outbound
--------
``ready``, ``message``, ``message_updated``, ``ack``, ``delivered``, ``read``,
``typing``, ``ping``, ``pong`` and ``error``. See the models below; the README
has the same table plus examples.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.security import (
    CLIENT_MESSAGE_ID_MAX_LENGTH,
    MESSAGE_MAX_LENGTH,
)
from app.schemas.chat import MessageRead
from app.schemas.user import UserPublic


class FrameError(Exception):
    """A frame that is not valid JSON or does not match the documented schema."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- inbound ----------------------------------------------------------------


class AuthFrame(BaseModel):
    """The mandatory first frame."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["auth"]
    token: str = Field(
        min_length=1,
        max_length=4096,
        description="Access token from POST /api/v1/auth/login.",
    )


class InboundMessage(BaseModel):
    """A message to store and fan out."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["message"]
    conversation_id: int = Field(ge=1)
    body: str | None = Field(
        default=None,
        min_length=1,
        max_length=MESSAGE_MAX_LENGTH,
        description="Optional text; a message needs text, media, or both.",
    )
    media_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description=(
            "Optional id from the media upload endpoint; it must belong to this "
            "conversation and to the sender, and be unattached."
        ),
    )
    client_message_id: str | None = Field(
        default=None,
        max_length=CLIENT_MESSAGE_ID_MAX_LENGTH,
        description=(
            "Optional idempotency key. Re-sending the same value in the same "
            "conversation returns the stored message instead of duplicating it."
        ),
    )

    @field_validator("body")
    @classmethod
    def _normalise_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @model_validator(mode="after")
    def _requires_content(self) -> "InboundMessage":
        if self.body is None and self.media_id is None:
            raise ValueError("A message must include text, media, or both.")
        return self

    @field_validator("client_message_id")
    @classmethod
    def _normalise_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = value.strip()
        return key or None


class PingFrame(BaseModel):
    """Client liveness probe."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["ping"]


class ReadFrame(BaseModel):
    """Mark incoming messages up to ``last_message_id`` as read."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["read"]
    conversation_id: int = Field(ge=1)
    last_message_id: int = Field(ge=1)


class TypingFrame(BaseModel):
    """Ephemeral typing state for a conversation (never stored)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["typing"]
    conversation_id: int = Field(ge=1)
    is_typing: bool = Field(
        default=True,
        description="Set false to clear the indicator before it expires.",
    )


class PongFrame(BaseModel):
    """Client answer to a server ``ping`` (keeps the socket alive)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["pong"]


InboundFrame = AuthFrame | InboundMessage | ReadFrame | TypingFrame | PingFrame | PongFrame

_INBOUND_MODELS: dict[str, type[BaseModel]] = {
    "auth": AuthFrame,
    "message": InboundMessage,
    "read": ReadFrame,
    "typing": TypingFrame,
    "ping": PingFrame,
    "pong": PongFrame,
}


def parse_frame(text: str) -> InboundFrame:
    """Parse one inbound frame, or raise :class:`FrameError`.

    Error messages never include the payload, so a malformed auth frame cannot
    echo its token back to the client or into a log.
    """
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise FrameError("invalid_payload", "Frame must be valid JSON.") from exc

    if not isinstance(payload, dict):
        raise FrameError("invalid_payload", "Frame must be a JSON object.")

    model = _INBOUND_MODELS.get(payload.get("type"))  # type: ignore[arg-type]
    if model is None:
        raise FrameError(
            "unknown_type",
            "Unknown frame type. Expected auth, message, read, typing, ping or pong.",
        )

    try:
        return model.model_validate(payload)  # type: ignore[return-value]
    except ValidationError as exc:
        raise FrameError(
            "invalid_payload",
            "Frame does not match the documented schema.",
        ) from exc


# --- outbound ---------------------------------------------------------------


class ReadyEvent(BaseModel):
    """Authentication succeeded; the socket is live."""

    type: Literal["ready"] = "ready"
    connection_id: str
    user: UserPublic
    conversations: list[int] = Field(
        description="Conversation ids the account participates in at connect time."
    )
    server_time: datetime


class MessageEvent(BaseModel):
    """A stored message, fanned out to every member's live connections."""

    type: Literal["message"] = "message"
    message: MessageRead


class MessageUpdatedEvent(BaseModel):
    """An existing message changed (currently: media was attached).

    Clients should replace the message with the same id instead of appending it.
    """

    type: Literal["message_updated"] = "message_updated"
    message: MessageRead


class AckEvent(BaseModel):
    """Delivery confirmation for the sending connection."""

    type: Literal["ack"] = "ack"
    client_message_id: str | None = None
    duplicate: bool = Field(
        description="True when an earlier send with the same client_message_id was reused."
    )
    message: MessageRead


class DeliveredEvent(BaseModel):
    """A message reached one of the recipient's live devices."""

    type: Literal["delivered"] = "delivered"
    conversation_id: int
    message_id: int = Field(
        description="Watermark: messages up to and including this id are delivered."
    )
    delivered_at: datetime


class ReadEvent(BaseModel):
    """The other participant advanced their read watermark.

    Fanned out to every connection of both participants; the initiating
    connection can treat it as the acknowledgement of its ``read`` frame.
    """

    type: Literal["read"] = "read"
    conversation_id: int
    message_id: int = Field(
        description="Watermark: messages up to and including this id are read."
    )
    reader_username: str
    read_at: datetime


class TypingEvent(BaseModel):
    """Ephemeral typing state (never stored; expires client-side)."""

    type: Literal["typing"] = "typing"
    conversation_id: int
    username: str
    is_typing: bool
    expires_in_seconds: int = Field(
        description="Hide the indicator automatically after this many seconds."
    )


class PingEvent(BaseModel):
    """Server liveness probe; the client should answer with ``pong``."""

    type: Literal["ping"] = "ping"
    server_time: datetime


class PongEvent(BaseModel):
    """Answer to either a client ``ping`` or a server ``ping``."""

    type: Literal["pong"] = "pong"
    server_time: datetime


class ErrorEvent(BaseModel):
    """A recoverable (or fatal, followed by a close frame) error."""

    type: Literal["error"] = "error"
    code: str
    message: str
    client_message_id: str | None = None
    retry_after_seconds: int | None = None
