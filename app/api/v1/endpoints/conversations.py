"""One-to-one chat endpoints.

Routes (all under ``/api/v1/conversations``):

* ``POST   /``                            — start (or reopen) a conversation
* ``GET    /``                            — page through my conversations
* ``GET    /{conversation_id}``           — one conversation I belong to
* ``POST   /{conversation_id}/messages``  — send a message
* ``GET    /{conversation_id}/messages``  — chronological history

Authorization is uniform: every route resolves the conversation through
:func:`app.services.chat.require_membership`, which answers ``404`` for a
conversation the caller does not participate in. That keeps the id from leaking
whether it exists.

``POST /`` is **idempotent**: creating the same pair twice returns the existing
conversation with ``200`` instead of a duplicate (``201`` is used only when a new
row was actually created). This is what makes the endpoint safe to retry after a
timeout, and it is how "one conversation per pair" is enforced at the API level
on top of the unique index.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.errors import NotFoundError
from app.db.models import Conversation, Message, User
from app.db.session import get_db
from app.schemas.chat import (
    ConversationCreate,
    ConversationDetail,
    ConversationPage,
    ConversationRead,
    MessageCreate,
    MessagePage,
    MessageRead,
    build_message,
)
from app.schemas.user import build_public_profile
from app.services import chat as chat_service
from app.services.profile import visible_last_seen
from app.services.user import get_user_by_username

router = APIRouter(prefix="/conversations", tags=["chat"])

_UNAUTHORIZED_RESPONSE = {
    "description": "Missing, invalid, expired or revoked credentials.",
    "content": {
        "application/json": {
            "example": {
                "error": {"code": "unauthorized", "message": "Could not validate credentials."}
            }
        }
    },
}

_DISABLED_RESPONSE = {
    "description": "The authenticated account is disabled.",
    "content": {
        "application/json": {
            "example": {
                "error": {"code": "forbidden", "message": "This account is disabled."}
            }
        }
    },
}

_NOT_FOUND_RESPONSE = {
    "description": (
        "No such conversation, or the caller is not a participant. Also returned "
        "for an unknown or deactivated target user."
    ),
    "content": {
        "application/json": {
            "example": {"error": {"code": "not_found", "message": "Conversation not found."}}
        }
    },
}

_FORBIDDEN_RESPONSE = {
    "description": (
        "The authenticated account is disabled, or a block exists between the "
        "two accounts (which refuses this operation in both directions)."
    ),
    "content": {
        "application/json": {
            "example": {"error": {"code": "forbidden", "message": "This user is not available."}}
        }
    },
}

_BAD_REQUEST_RESPONSE = {
    "description": "The request cannot be carried out (for example talking to yourself).",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "bad_request",
                    "message": "You cannot start a conversation with yourself.",
                }
            }
        }
    },
}

_VALIDATION_RESPONSE = {
    "description": "The submitted payload is invalid (empty or too long message, unknown field).",
    "content": {
        "application/json": {
            "example": {
                "error": {"code": "validation_error", "message": "Request validation failed."}
            }
        }
    },
}

_RATE_LIMIT_RESPONSE = {
    "description": "Too many chat requests. Retry after the ``Retry-After`` interval.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please try again later.",
                }
            }
        }
    },
}

#: Common error responses for the read endpoints.
_READ_ERRORS = {
    401: _UNAUTHORIZED_RESPONSE,
    403: _DISABLED_RESPONSE,
    404: _NOT_FOUND_RESPONSE,
    422: _VALIDATION_RESPONSE,
    429: _RATE_LIMIT_RESPONSE,
}


def _conversation_summary(
    conversation: Conversation,
    *,
    viewer: User,
    participant: User,
    last: Message | None,
) -> ConversationRead:
    """Assemble a conversation for output.

    In a two-person conversation the sender of the last message is either the
    viewer or the other participant, so the username is derived locally instead
    of querying it again.
    """
    last_message = None
    if last is not None:
        sender_username = (
            viewer.username if last.sender_id == viewer.id else participant.username
        )
        last_message = build_message(last, sender_username=sender_username)

    return ConversationRead(
        id=conversation.id,
        participant=build_public_profile(
            participant,
            last_seen=visible_last_seen(viewer=viewer, target=participant),
        ),
        last_message=last_message,
        created_at=conversation.created_at,
        last_message_at=conversation.last_message_at,
    )


def _conversation_detail(
    db: Session,
    conversation: Conversation,
    *,
    viewer: User,
    participant: User,
) -> ConversationDetail:
    summary = _conversation_summary(
        conversation,
        viewer=viewer,
        participant=participant,
        last=chat_service.last_message(db, conversation_id=conversation.id),
    )
    return ConversationDetail(
        **summary.model_dump(),
        message_count=chat_service.count_messages(db, conversation_id=conversation.id),
    )


@router.post(
    "",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
    description=(
        "Open a private conversation with ``username``. Repeating the request — "
        "from either side — returns the existing conversation with ``200`` "
        "instead of creating a duplicate (``201`` means a new one was created). "
        "Conversations with yourself, with an unknown or deactivated account, "
        "or across a block are rejected."
    ),
    responses={
        200: {
            "model": ConversationDetail,
            "description": (
                "The pair already had a conversation; the existing one is returned "
                "instead of creating a duplicate."
            ),
        },
        400: _BAD_REQUEST_RESPONSE,
        401: _UNAUTHORIZED_RESPONSE,
        403: _FORBIDDEN_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        422: _VALIDATION_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def create_conversation(
    payload: ConversationCreate,
    request: Request,
    response: Response,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ConversationDetail:
    """Start or reopen the conversation between the caller and ``username``."""
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")

    target = get_user_by_username(db, payload.username)
    if target is None:
        raise NotFoundError("User not found.")

    conversation, created = chat_service.get_or_create_conversation(
        db, initiator=current_user, target=target
    )
    if not created:
        # Not a creation: the pair already had a conversation.
        response.status_code = status.HTTP_200_OK

    return _conversation_detail(db, conversation, viewer=current_user, participant=target)


@router.get(
    "",
    response_model=ConversationPage,
    summary="List my conversations",
    description=(
        "Page through the conversations I participate in, most recent activity "
        "first. Only my own conversations are visible; each item contains the "
        "other participant's public profile and a preview of the newest message."
    ),
    responses=_READ_ERRORS,
)
def list_my_conversations(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="Conversations per page."),
    offset: int = Query(default=0, ge=0, description="Conversations to skip."),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ConversationPage:
    """Return a page of the caller's conversations."""
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")

    effective_limit = min(limit, settings.conversation_max_limit)
    conversations = chat_service.list_conversations(
        db,
        user_id=current_user.id,
        limit=effective_limit,
        offset=offset,
    )
    conversation_ids = [conversation.id for conversation in conversations]
    participants = chat_service.other_participants(
        db,
        conversation_ids=conversation_ids,
        viewer_id=current_user.id,
    )
    latest = chat_service.latest_messages(db, conversation_ids=conversation_ids)

    items = []
    for conversation in conversations:
        participant = participants.get(conversation.id)
        if participant is None:  # pragma: no cover - defensive: malformed row
            continue
        items.append(
            _conversation_summary(
                conversation,
                viewer=current_user,
                participant=participant,
                last=latest.get(conversation.id),
            )
        )

    return ConversationPage(
        items=items,
        total=chat_service.count_conversations(db, user_id=current_user.id),
        limit=effective_limit,
        offset=offset,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get a conversation",
    description="Return one conversation I participate in, with its message count.",
    responses=_READ_ERRORS,
)
def get_conversation(
    conversation_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ConversationDetail:
    """Return a conversation, or 404 when I am not a participant."""
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")

    conversation = chat_service.require_membership(
        db, conversation_id=conversation_id, user_id=current_user.id
    )
    participant = chat_service.other_participant(
        db, conversation_id=conversation.id, viewer_id=current_user.id
    )
    return _conversation_detail(db, conversation, viewer=current_user, participant=participant)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Send a message",
    description=(
        "Append a text message to a conversation I participate in. The body is "
        "trimmed and must be 1-4000 characters; an empty or whitespace-only body "
        "is rejected. Sending is refused when either participant has blocked the "
        "other, even if the conversation already existed."
    ),
    responses={
        400: _BAD_REQUEST_RESPONSE,
        401: _UNAUTHORIZED_RESPONSE,
        403: _FORBIDDEN_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        422: _VALIDATION_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def send_message(
    conversation_id: int,
    payload: MessageCreate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageRead:
    """Store a message from the caller in the given conversation."""
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")

    conversation = chat_service.require_membership(
        db, conversation_id=conversation_id, user_id=current_user.id
    )
    message = chat_service.send_message(
        db,
        conversation=conversation,
        sender=current_user,
        body=payload.body,
    )
    return build_message(message, sender_username=current_user.username)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagePage,
    summary="Read message history",
    description=(
        "Return a chronological page of messages (oldest first, ordered by the "
        "monotonic message id so the order is stable even when timestamps "
        "collide). Only participants may read a conversation; anyone else gets "
        "``404``."
    ),
    responses=_READ_ERRORS,
)
def list_messages(
    conversation_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200, description="Messages per page."),
    offset: int = Query(default=0, ge=0, description="Messages to skip."),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessagePage:
    """Return a page of a conversation's messages."""
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")

    conversation = chat_service.require_membership(
        db, conversation_id=conversation_id, user_id=current_user.id
    )
    effective_limit = min(limit, settings.message_max_limit)
    messages = chat_service.list_messages(
        db,
        conversation_id=conversation.id,
        limit=effective_limit,
        offset=offset,
    )
    participant = chat_service.other_participant(
        db, conversation_id=conversation.id, viewer_id=current_user.id
    )

    items = [
        build_message(
            message,
            sender_username=(
                current_user.username
                if message.sender_id == current_user.id
                else participant.username
            ),
        )
        for message in messages
    ]

    return MessagePage(
        items=items,
        total=chat_service.count_messages(db, conversation_id=conversation.id),
        limit=effective_limit,
        offset=offset,
    )
