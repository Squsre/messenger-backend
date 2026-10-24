"""One-to-one conversations and messages.

Business rules live here so the endpoints stay thin and the same rules apply to
every entry point:

* a conversation always has exactly **two** participants and is identified by a
  canonical ``pair_key``, so a duplicate pair can never be created (the unique
  index is the hard guarantee; the lookup below is only the fast path),
* starting a conversation with yourself is a ``400``, with an unknown or
  deactivated account a ``404``, and across a block a ``403`` — the same
  vocabulary the contacts phase uses,
* every read and write is authorized through :func:`require_membership`, which
  reports a conversation the caller does not belong to as ``404`` (not ``403``)
  so ids cannot be probed,
* the block rules apply again when a message is sent, because a block can be
  created *after* the conversation exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.security import CLIENT_MESSAGE_ID_MAX_LENGTH, MESSAGE_MAX_LENGTH
from app.db.models import Conversation, ConversationMember, Media, Message, User
from app.services.contact import is_blocked_between
from app.services.media import attach_to_message


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- conversations ----------------------------------------------------------


def pair_key_for(first_id: int, second_id: int) -> str:
    """Canonical label for a pair of participants, independent of who asks."""
    low, high = sorted((first_id, second_id))
    return f"{low}:{high}"


def get_conversation(db: Session, conversation_id: int) -> Conversation | None:
    return db.get(Conversation, conversation_id)


def is_member(db: Session, *, conversation_id: int, user_id: int) -> bool:
    """Whether ``user`` participates in ``conversation``."""
    statement = select(ConversationMember.id).where(
        ConversationMember.conversation_id == conversation_id,
        ConversationMember.user_id == user_id,
    )
    return db.execute(statement).scalar_one_or_none() is not None


def require_membership(db: Session, *, conversation_id: int, user_id: int) -> Conversation:
    """Return the conversation, or ``404`` when the caller is not a participant.

    ``404`` rather than ``403`` is deliberate: a non-participant must not be able
    to tell an existing conversation id from a non-existent one.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or not is_member(
        db, conversation_id=conversation_id, user_id=user_id
    ):
        raise NotFoundError("Conversation not found.")
    return conversation


def get_or_create_conversation(
    db: Session,
    *,
    initiator: User,
    target: User,
) -> tuple[Conversation, bool]:
    """Return the pair's conversation, creating it on first use.

    Returns ``(conversation, created)``. Repeating the call — by either
    participant — returns the same row with ``created=False`` instead of a
    duplicate, which is what makes the endpoint safe to retry.
    """
    if initiator.id == target.id:
        raise BadRequestError("You cannot start a conversation with yourself.")
    if not target.is_active:
        # Same answer as an unknown username: deactivated accounts are invisible.
        raise NotFoundError("User not found.")
    if is_blocked_between(db, initiator.id, target.id):
        raise ForbiddenError("This user is not available.")

    pair_key = pair_key_for(initiator.id, target.id)
    existing = db.execute(
        select(Conversation).where(Conversation.pair_key == pair_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    try:
        # The row(s) are added *inside* the savepoint, so losing the race rolls
        # back only this attempt and leaves the request transaction usable.
        with db.begin_nested():
            conversation = Conversation(pair_key=pair_key)
            db.add(conversation)
            db.flush()
            db.add_all(
                [
                    ConversationMember(conversation_id=conversation.id, user_id=initiator.id),
                    ConversationMember(conversation_id=conversation.id, user_id=target.id),
                ]
            )
            db.flush()
    except IntegrityError:
        # A concurrent request created the same pair first; use theirs.
        conversation = db.execute(
            select(Conversation).where(Conversation.pair_key == pair_key)
        ).scalar_one()
        return conversation, False

    return conversation, True


def _member_conversations(user_id: int) -> Select[tuple[Conversation]]:
    """Conversations ``user_id`` participates in."""
    return (
        select(Conversation)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(ConversationMember.user_id == user_id)
    )


def list_conversations(
    db: Session,
    *,
    user_id: int,
    limit: int,
    offset: int,
) -> list[Conversation]:
    """Page through a user's conversations, most recent activity first.

    ``coalesce`` keeps conversations that have no messages yet (``NULL``
    ``last_message_at``) ordered by their creation time instead of sorting them
    unpredictably, and works on SQLite and PostgreSQL alike.
    """
    statement = (
        _member_conversations(user_id)
        .order_by(
            func.coalesce(Conversation.last_message_at, Conversation.created_at).desc(),
            Conversation.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_conversations(db: Session, *, user_id: int) -> int:
    subquery = _member_conversations(user_id).subquery()
    return int(db.execute(select(func.count()).select_from(subquery)).scalar_one())


def other_participant(db: Session, *, conversation_id: int, viewer_id: int) -> User:
    """The participant who is not ``viewer``."""
    statement = (
        select(User)
        .join(ConversationMember, ConversationMember.user_id == User.id)
        .where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id != viewer_id,
        )
        .limit(1)
    )
    participant = db.execute(statement).scalars().first()
    if participant is None:
        # A malformed conversation (no second member) is treated like a missing
        # one rather than leaking an internal inconsistency.
        raise NotFoundError("Conversation not found.")
    return participant


def other_participants(
    db: Session,
    *,
    conversation_ids: list[int],
    viewer_id: int,
) -> dict[int, User]:
    """Bulk variant of :func:`other_participant`, keyed by conversation id.

    Avoids an N+1 query when building a page of conversations.
    """
    if not conversation_ids:
        return {}

    statement = (
        select(ConversationMember.conversation_id, User)
        .join(User, User.id == ConversationMember.user_id)
        .where(
            ConversationMember.conversation_id.in_(conversation_ids),
            ConversationMember.user_id != viewer_id,
        )
    )
    return {conversation_id: user for conversation_id, user in db.execute(statement).all()}


def conversation_member_ids(db: Session, *, conversation_id: int) -> list[int]:
    """User ids of every participant in a conversation.

    Used by the realtime layer to decide which live connections may receive an
    event. The answer always comes from the database, so a connection cannot
    widen its own audience.
    """
    statement = select(ConversationMember.user_id).where(
        ConversationMember.conversation_id == conversation_id
    )
    return list(db.execute(statement).scalars().all())


def conversation_ids_for(db: Session, *, user_id: int) -> list[int]:
    """Conversation ids the user participates in (for the ``ready`` event)."""
    statement = (
        select(ConversationMember.conversation_id)
        .where(ConversationMember.user_id == user_id)
        .order_by(ConversationMember.conversation_id.asc())
    )
    return list(db.execute(statement).scalars().all())


# --- messages ---------------------------------------------------------------


def latest_messages(db: Session, *, conversation_ids: list[int]) -> dict[int, Message]:
    """The newest message of each conversation, keyed by conversation id."""
    if not conversation_ids:
        return {}

    newest_ids = (
        select(func.max(Message.id))
        .where(Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
    )
    statement = select(Message).where(Message.id.in_(newest_ids))
    return {message.conversation_id: message for message in db.execute(statement).scalars().all()}


def last_message(db: Session, *, conversation_id: int) -> Message | None:
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    return db.execute(statement).scalars().first()


def send_message(
    db: Session,
    *,
    conversation: Conversation,
    sender: User,
    body: str | None,
    client_message_id: str | None = None,
    media: Media | None = None,
) -> tuple[Message, bool]:
    """Append a text and/or media message to a conversation.

    Returns ``(message, created)``. ``created`` is ``False`` when the same
    sender already sent this ``client_message_id`` in this conversation — a
    realtime client retrying after a dropped connection gets the stored row back
    instead of a duplicate.

    At least one of ``body`` and ``media`` must be present. Media must belong to
    the same conversation, to the sender, be ready, and (for a new message) be
    unattached. These checks are repeated here, not only in the endpoint, so the
    WebSocket path and any future caller get the same rules.

    Access rules are checked **before** the idempotent lookup: after either side
    blocks the other, a retry is rejected with ``403`` and never returns the
    old message as if the send had just succeeded. Reusing a key with a
    different body **or media** is ``409`` (see :func:`_replay`).

    The caller must already have been authorized with :func:`require_membership`.
    Blocking is re-checked here because it can happen *after* the conversation
    was created: a blocked pair may keep reading its history but cannot exchange
    new messages.
    """
    text = body.strip() if body is not None else None
    if text == "":
        text = None
    if text is not None and len(text) > MESSAGE_MAX_LENGTH:
        raise BadRequestError(
            f"Message body must be at most {MESSAGE_MAX_LENGTH} characters."
        )
    if text is None and media is None:
        raise BadRequestError("A message must include text, media, or both.")

    if media is not None:
        if (
            media.conversation_id != conversation.id
            or media.uploader_id != sender.id
            or media.status != "ready"
        ):
            raise NotFoundError("Media not found.")

    key = client_message_id.strip() if client_message_id else None
    if not key:
        key = None
    if key is not None and len(key) > CLIENT_MESSAGE_ID_MAX_LENGTH:
        raise BadRequestError(
            "client_message_id must be at most "
            f"{CLIENT_MESSAGE_ID_MAX_LENGTH} characters."
        )

    # Block rules are evaluated *before* an idempotent replay. A blocked pair
    # must never receive a success-shaped response for a retry: the old message
    # stays in history, but the socket cannot use retries to keep a send path
    # open after the block.
    recipient = other_participant(
        db, conversation_id=conversation.id, viewer_id=sender.id
    )
    if is_blocked_between(db, sender.id, recipient.id):
        raise ForbiddenError("This user is not available.")

    if key is not None:
        existing = db.execute(
            _message_by_client_id(conversation.id, sender.id, key)
        ).scalar_one_or_none()
        if existing is not None:
            return _replay(db, existing, text, media), False

    if media is not None and media.message_id is not None:
        raise ConflictError("This media is already attached to a message.")

    message = Message(
        conversation_id=conversation.id,
        sender_id=sender.id,
        body=text,
        client_message_id=key,
        # Set explicitly (the column also has a server default for raw inserts)
        # so the response and `last_message_at` agree without an extra SELECT.
        created_at=utcnow(),
    )
    try:
        # The row is added *inside* the savepoint so a concurrent retry with the
        # same key can roll back just this insert and reuse the stored message.
        with db.begin_nested():
            db.add(message)
            db.flush()
    except IntegrityError:
        if key is None:  # pragma: no cover - only the unique index can conflict
            raise
        existing = db.execute(
            _message_by_client_id(conversation.id, sender.id, key)
        ).scalar_one()
        return _replay(db, existing, text, media), False

    if media is not None:
        attach_to_message(db, media=media, message=message)

    conversation.last_message_at = message.created_at
    db.flush()
    return message, True


def _replay(
    db: Session,
    existing: Message,
    body: str | None,
    media: Media | None,
) -> Message:
    """Validate an idempotent retry and return the stored message.

    The first successful payload for a ``client_message_id`` is immutable: a
    retry with *different* text or a different attachment is a client bug or an
    attempt to smuggle new content under an old key, so it is refused with
    ``409`` instead of being ignored.

    The stored media id is read as a scalar on purpose: the caller may already
    have loaded the same ``Media`` row before a concurrent winner attached it,
    and the identity map could otherwise hide that update.
    """
    stored_media_id = db.execute(
        select(Media.id).where(Media.message_id == existing.id)
    ).scalar_one_or_none()
    requested_media_id = media.id if media is not None else None
    if existing.body != body or stored_media_id != requested_media_id:
        raise ConflictError(
            "client_message_id was already used with a different message."
        )
    return existing


def _message_by_client_id(
    conversation_id: int, sender_id: int, client_message_id: str
) -> Select[tuple[Message]]:
    return select(Message).where(
        Message.conversation_id == conversation_id,
        Message.sender_id == sender_id,
        Message.client_message_id == client_message_id,
    )


# --- delivery and read state ------------------------------------------------


@dataclass(frozen=True)
class ReadState:
    """Result of marking a conversation read up to a message id."""

    last_read_message_id: int
    #: ``None`` when nothing new needed updating.
    read_at: datetime | None
    #: Messages that actually changed (0 when everything was already read).
    updated: int
    unread_count: int


def mark_read(
    db: Session,
    *,
    conversation_id: int,
    reader_id: int,
    last_message_id: int,
) -> ReadState:
    """Mark every message up to ``last_message_id`` as read for ``reader_id``.

    Only messages *from the other participant* are considered: a user's own
    messages are never "unread" for them. Reading implies delivery, so
    ``delivered_at`` is filled in when it was still ``NULL``.

    The target id must exist **in this conversation**. A caller cannot use an id
    from another conversation (or one that does not exist) to move the watermark:
    that is a ``404``, consistent with the rest of the chat API.
    """
    target = db.execute(
        select(Message.id).where(
            Message.id == last_message_id,
            Message.conversation_id == conversation_id,
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError("Message not found.")

    now = utcnow()
    result = db.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_id != reader_id,
            Message.id <= last_message_id,
            Message.read_at.is_(None),
        )
        .values(
            read_at=now,
            delivered_at=func.coalesce(Message.delivered_at, now),
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()
    updated = int(result.rowcount or 0)
    return ReadState(
        last_read_message_id=last_message_id,
        read_at=now if updated else None,
        updated=updated,
        unread_count=unread_count(
            db, conversation_id=conversation_id, viewer_id=reader_id
        ),
    )


def unread_count(db: Session, *, conversation_id: int, viewer_id: int) -> int:
    """Unread messages from the other participant in one conversation."""
    return unread_counts(
        db, user_id=viewer_id, conversation_ids=[conversation_id]
    ).get(conversation_id, 0)


def unread_counts(
    db: Session, *, user_id: int, conversation_ids: list[int]
) -> dict[int, int]:
    """Unread counts for several conversations, keyed by conversation id.

    Joining :class:`ConversationMember` is not just an optimisation: it makes
    the database itself refuse to count conversations the caller does not belong
    to, even if a caller passes an id it should not know.
    """
    if not conversation_ids:
        return {}

    statement = (
        select(Message.conversation_id, func.count())
        .join(
            ConversationMember,
            ConversationMember.conversation_id == Message.conversation_id,
        )
        .where(
            Message.conversation_id.in_(conversation_ids),
            ConversationMember.user_id == user_id,
            Message.sender_id != user_id,
            Message.read_at.is_(None),
        )
        .group_by(Message.conversation_id)
    )
    return {
        conversation_id: int(total)
        for conversation_id, total in db.execute(statement).all()
    }


def list_messages(
    db: Session,
    *,
    conversation_id: int,
    limit: int,
    offset: int,
) -> list[Message]:
    """A chronological page of messages.

    Ordered by the monotonic primary key, which is both chronological (ids are
    assigned in insert order) and stable (timestamps can collide within the same
    second, ids cannot).
    """
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_messages(db: Session, *, conversation_id: int) -> int:
    statement = (
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id)
    )
    return int(db.execute(statement).scalar_one())
