"""Persistence and authorization rules for uploaded media.

A media row is always scoped to **one conversation and one uploader**. The
binary lives in the storage backend, never in PostgreSQL. A row may be
attached to at most one message (``media.message_id`` is unique), which is what
makes "one attachment per message" a database guarantee rather than a UI
convention.

The public API exposes ``public_id`` (128 random bits), not the internal
integer id and never the storage key. All lookups go through a membership or
ownership query, so an id from another conversation is indistinguishable from a
missing one.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.db.models import (
    Conversation,
    ConversationMember,
    Media,
    Message,
    User,
)
from app.services.media_validation import PreparedUpload
from app.services.storage import StorageService, new_storage_key

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_public_id() -> str:
    """128-bit, URL-safe-enough identifier (32 lowercase hex characters)."""
    return secrets.token_hex(16)


def get_media(db: Session, *, public_id: str) -> Media | None:
    return db.execute(
        select(Media).where(Media.public_id == public_id)
    ).scalar_one_or_none()


def require_media_for_member(db: Session, *, public_id: str, user_id: int) -> Media:
    """Return media visible to a conversation member, else ``404``.

    A non-member must not learn whether the id exists, so both "no such media"
    and "not in this conversation" answer with the same ``404``.
    """
    media = get_media(db, public_id=public_id)
    if media is None:
        raise NotFoundError("Media not found.")
    membership = db.execute(
        select(ConversationMember.id).where(
            ConversationMember.conversation_id == media.conversation_id,
            ConversationMember.user_id == user_id,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Media not found.")
    return media


def require_owned_media(
    db: Session,
    *,
    public_id: str,
    conversation_id: int,
    uploader_id: int,
) -> Media:
    """Return media owned by ``uploader_id`` in ``conversation_id``.

    Used before attaching media to a message. Media owned by another user or
    scoped to another conversation is hidden behind the same ``404``.
    """
    media = db.execute(
        select(Media).where(
            Media.public_id == public_id,
            Media.conversation_id == conversation_id,
            Media.uploader_id == uploader_id,
        )
    ).scalar_one_or_none()
    if media is None:
        raise NotFoundError("Media not found.")
    return media


def media_for_message(db: Session, *, message_id: int) -> Media | None:
    return db.execute(
        select(Media).where(Media.message_id == message_id)
    ).scalar_one_or_none()


def media_by_message_ids(
    db: Session, *, message_ids: list[int]
) -> dict[int, Media]:
    """Bulk lookup for history/list rendering (avoids an N+1 query)."""
    if not message_ids:
        return {}
    statement = select(Media).where(Media.message_id.in_(message_ids))
    return {
        int(media.message_id): media
        for media in db.execute(statement).scalars().all()
        if media.message_id is not None
    }


def store_uploads(
    db: Session,
    *,
    conversation: Conversation,
    uploader: User,
    uploads: list[PreparedUpload],
    storage: StorageService,
) -> list[Media]:
    """Write files, then their rows, and clean up on any failure.

    The binary is written first (atomically, under a server-generated key) and
    the rows are committed together. If validation, storage or the database
    fails, every key written by this request is removed again. A process crash
    between the two steps can still leave an orphan object; the conservative
    ``app.tools.media_orphans`` reconciler reports and removes those only after
    a grace period, as documented in the README.
    """
    written: list[str] = []
    rows: list[Media] = []
    try:
        backend_name = getattr(storage, "backend_name", "local")
        for prepared in uploads:
            key = new_storage_key(prepared.extension)
            # Record the key before writing: if the backend fails after creating
            # the object, cleanup still knows what to remove.
            written.append(key)
            stored = storage.save(
                key,
                prepared.data,
                content_type=prepared.content_type,
            )
            checksum = prepared.checksum_sha256 or hashlib.sha256(
                prepared.data
            ).hexdigest()
            rows.append(
                Media(
                    public_id=new_public_id(),
                    storage_key=key,
                    original_filename=prepared.original_filename,
                    content_type=prepared.content_type,
                    size_bytes=prepared.size_bytes,
                    width=prepared.width,
                    height=prepared.height,
                    status="ready",
                    storage_backend=backend_name,
                    checksum_sha256=checksum,
                    etag=getattr(stored, "etag", None),
                    conversation_id=conversation.id,
                    uploader_id=uploader.id,
                    created_at=utcnow(),
                )
            )
        db.add_all(rows)
        db.commit()
    except Exception:
        db.rollback()
        for key in written:
            try:
                storage.delete(key)
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.exception("Could not clean up media object %s", key)
        raise
    return rows


def attach_to_message(db: Session, *, media: Media, message: Message) -> None:
    """Atomically attach ``media`` to ``message``.

    The conditional ``UPDATE`` is what prevents two concurrent sends from moving
    the same media row onto two messages: only the request that sees
    ``message_id IS NULL`` updates one row. The unique constraint on
    ``media.message_id`` additionally keeps a single message from holding two
    attachments.
    """
    if media.message_id is not None and media.message_id != message.id:
        raise ConflictError("This media is already attached to a message.")
    if media.message_id == message.id:
        return

    existing = media_for_message(db, message_id=message.id)
    if existing is not None:
        raise ConflictError("This message already has media.")

    try:
        result = db.execute(
            update(Media)
            .where(Media.id == media.id, Media.message_id.is_(None))
            .values(message_id=message.id)
            .execution_options(synchronize_session=False)
        )
    except IntegrityError as exc:
        raise ConflictError("This message already has media.") from exc
    if int(result.rowcount or 0) != 1:
        raise ConflictError("This media is already attached to a message.")
    media.message_id = message.id
    db.flush()
