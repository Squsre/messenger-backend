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

from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.core.security import MESSAGE_MAX_LENGTH
from app.db.models import Conversation, ConversationMember, Message, User
from app.services.contact import is_blocked_between


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
    body: str,
) -> Message:
    """Append a message to a conversation.

    The caller must already have been authorized with :func:`require_membership`.
    Blocking is re-checked here because it can happen after the conversation was
    created: a blocked pair may keep reading its history but cannot exchange new
    messages.
    """
    text = body.strip()
    if not text:
        raise BadRequestError("Message body must not be empty.")
    if len(text) > MESSAGE_MAX_LENGTH:
        raise BadRequestError(
            f"Message body must be at most {MESSAGE_MAX_LENGTH} characters."
        )

    recipient = other_participant(
        db, conversation_id=conversation.id, viewer_id=sender.id
    )
    if is_blocked_between(db, sender.id, recipient.id):
        raise ForbiddenError("This user is not available.")

    message = Message(
        conversation_id=conversation.id,
        sender_id=sender.id,
        body=text,
        # Set explicitly (the column also has a server default for raw inserts)
        # so the response and `last_message_at` agree without an extra SELECT.
        created_at=utcnow(),
    )
    db.add(message)
    conversation.last_message_at = message.created_at
    db.flush()
    return message


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
