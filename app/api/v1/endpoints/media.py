"""Authenticated media upload, download and attachment endpoints.

Routes (all under ``/api/v1``):

* ``POST   /conversations/{conversation_id}/media`` — upload 1..N files
* ``GET    /media/{media_id}``                      — metadata
* ``GET    /media/{media_id}/content``              — authenticated bytes
* ``DELETE /media/{media_id}``                      — delete an unattached upload
* ``POST   /media/{media_id}/attach``               — attach to one of my messages

Every route checks conversation membership. Upload and delete are additionally
scoped to the uploader. The content route never exposes a filesystem path and
sends ``Content-Disposition: attachment`` plus ``nosniff``; uploaded types are
an allow-list, so HTML/SVG/executables are never served as active content.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api import deps
from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
)
from app.db.models import Message, User
from app.db.session import get_db
from app.schemas.chat import build_message
from app.schemas.media import (
    AttachMediaRequest,
    MediaBatchRead,
    MediaRead,
    build_media,
)
from app.schemas.realtime import MessageUpdatedEvent
from app.services import chat as chat_service
from app.services import media as media_service
from app.services.contact import is_blocked_between
from app.services.media_validation import prepare_upload
from app.services.storage import StorageError, StorageObjectNotFound

logger = logging.getLogger(__name__)

router = APIRouter()

_UNAUTHORIZED = {
    "description": "Missing, invalid, expired or revoked credentials.",
}
_FORBIDDEN = {
    "description": "The account is disabled, or the caller is not the uploader.",
}
_NOT_FOUND = {
    "description": "No such media/message, or the caller is not a conversation member.",
}
_CONFLICT = {
    "description": "The media is already attached, or the message already has media.",
}
_TOO_LARGE = {
    "description": "The file exceeds MEDIA_MAX_UPLOAD_BYTES.",
}
_UNSUPPORTED = {
    "description": "The filename, declared type or content is not on the allow-list.",
}
_RATE_LIMITED = {
    "description": "Too many media requests. Retry after the ``Retry-After`` interval.",
}

_MEDIA_ERRORS = {
    401: _UNAUTHORIZED,
    403: _FORBIDDEN,
    404: _NOT_FOUND,
    409: _CONFLICT,
    413: _TOO_LARGE,
    415: _UNSUPPORTED,
    429: _RATE_LIMITED,
}


def _content_disposition(filename: str) -> str:
    """Build a header-safe attachment disposition for a display filename."""
    # The display name was validated at upload time, but it may come from an old
    # or manually edited row. Keep the ASCII fallback to printable characters so
    # a newline or quote can never break out of the header; the RFC 5987 form
    # carries the full Unicode name.
    fallback = "".join(
        character
        if " " <= character <= "~" and character not in {'"', "\\"}
        else "_"
        for character in filename
    ).strip() or "file"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


@router.post(
    "/conversations/{conversation_id}/media",
    response_model=MediaBatchRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload photos or files to a conversation",
    description=(
        "Upload between 1 and ``MEDIA_MAX_FILES_PER_REQUEST`` files to a "
        "conversation I participate in. Each file is limited to "
        "``MEDIA_MAX_UPLOAD_BYTES``. The declared MIME type and the extension "
        "must agree with the content detected from the bytes; allowed formats "
        "are JPEG, PNG, GIF, WebP, PDF, TXT and CSV. Executables, HTML, SVG and "
        "unknown/binary content are rejected.\n\n"
        "The returned opaque ``id`` is used with the message endpoints. Files are "
        "stored outside the database and outside the application package under a "
        "server-generated random key; the original filename is only a display "
        "label."
    ),
    responses={
        201: {"description": "All files were validated and stored."},
        400: {"description": "Empty upload, too many files or an unsafe filename."},
        **_MEDIA_ERRORS,
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": (
                                    "One or more files. The server enforces "
                                    "MEDIA_MAX_FILES_PER_REQUEST while parsing and "
                                    "MEDIA_MAX_UPLOAD_BYTES per file."
                                ),
                            }
                        },
                        "required": ["files"],
                    }
                }
            },
        }
    },
)
async def upload_media(
    conversation_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage=Depends(deps.get_storage),
) -> MediaBatchRead:
    """Validate and store files, then return their opaque metadata."""
    deps.enforce_rate_limit(request, settings, "media", f"user:{current_user.id}")

    conversation = chat_service.require_membership(
        db, conversation_id=conversation_id, user_id=current_user.id
    )

    # Parse with the configured file cap *inside* Starlette, before it spools an
    # arbitrary number of parts to temporary files. The endpoint-level check
    # below is a second line of defence and gives a clearer error for an empty
    # field.
    try:
        form = await request.form(
            max_files=settings.media_max_files_per_request,
            max_fields=settings.media_max_files_per_request + 1,
        )
    except MultiPartException as exc:
        raise BadRequestError(
            "At most "
            f"{settings.media_max_files_per_request} files may be uploaded at once."
        ) from exc

    values = form.getlist("files")
    files = [item for item in values if isinstance(item, UploadFile)]
    if not files or len(files) != len(values):
        raise BadRequestError("At least one file is required in the 'files' field.")

    prepared = []
    try:
        for upload in files:
            filename = upload.filename
            declared = upload.content_type
            data = await upload.read(settings.media_max_upload_bytes + 1)
            if len(data) > settings.media_max_upload_bytes:
                raise PayloadTooLargeError(
                    "The uploaded file exceeds the configured size limit."
                )
            prepared.append(
                prepare_upload(
                    filename=filename,
                    declared_content_type=declared,
                    data=data,
                    settings=settings,
                )
            )
    finally:
        # Close every parsed part even when an earlier file is invalid.
        for upload in files:
            await upload.close()

    rows = media_service.store_uploads(
        db,
        conversation=conversation,
        uploader=current_user,
        uploads=prepared,
        storage=storage,
    )
    return MediaBatchRead(
        items=[
            build_media(row, uploader_username=current_user.username)
            for row in rows
        ]
    )


@router.get(
    "/media/{media_id}",
    response_model=MediaRead,
    summary="Get media metadata",
    description=(
        "Return metadata for a file in a conversation I participate in. Files "
        "from other conversations return ``404``, never ``403``, so ids cannot "
        "be probed. The storage key is never returned."
    ),
    responses=_MEDIA_ERRORS,
)
def get_media(
    media_id: str,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MediaRead:
    """Return one file's safe metadata."""
    deps.enforce_rate_limit(request, settings, "media", f"user:{current_user.id}")
    media = media_service.require_media_for_member(
        db, public_id=media_id, user_id=current_user.id
    )
    uploader = db.get(User, media.uploader_id)
    return build_media(
        media,
        uploader_username=uploader.username if uploader is not None else current_user.username,
    )


@router.get(
    "/media/{media_id}/content",
    summary="Download a file",
    description=(
        "Stream the stored bytes to a conversation member. Authentication uses "
        "the normal Bearer header. Responses are served as attachments with "
        "``X-Content-Type-Options: nosniff`` and are never cached by shared "
        "proxies. A missing database row or missing object both return ``404``."
    ),
    responses={
        **_MEDIA_ERRORS,
        200: {
            "description": "The stored file.",
            "content": {"application/octet-stream": {}},
        },
    },
)
def download_media(
    media_id: str,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage=Depends(deps.get_storage),
) -> Response:
    """Return the authenticated file bytes."""
    deps.enforce_rate_limit(request, settings, "media", f"user:{current_user.id}")
    media = media_service.require_media_for_member(
        db, public_id=media_id, user_id=current_user.id
    )
    if media.status != "ready":
        raise ConflictError("This media is not available.")

    try:
        data = storage.read(media.storage_key)
    except StorageObjectNotFound as exc:
        raise NotFoundError("Media not found.") from exc
    except StorageError:
        logger.exception("Could not read stored media object")
        raise

    return Response(
        content=data,
        media_type=media.content_type,
        headers={
            "Content-Disposition": _content_disposition(media.original_filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store, max-age=0",
        },
    )


@router.delete(
    "/media/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unattached upload",
    description=(
        "Delete a file I uploaded that is not attached to a message. Attached "
        "media is part of message history and returns ``409``; there is no "
        "detach endpoint in this phase. A missing object on disk is tolerated so "
        "the row can still be cleaned up."
    ),
    responses=_MEDIA_ERRORS,
)
def delete_media(
    media_id: str,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage=Depends(deps.get_storage),
) -> Response:
    """Delete an unattached media row and its object."""
    deps.enforce_rate_limit(request, settings, "media", f"user:{current_user.id}")
    media = media_service.require_media_for_member(
        db, public_id=media_id, user_id=current_user.id
    )
    if media.uploader_id != current_user.id:
        raise ForbiddenError("Only the uploader can delete this media.")
    if media.message_id is not None:
        raise ConflictError("Media attached to a message cannot be deleted.")

    try:
        storage.delete(media.storage_key)
    except StorageError:
        # Keep the row: without the object the metadata is useless, but a retry
        # can still complete instead of leaving an untracked file behind.
        logger.exception("Could not delete stored media object")
        raise

    db.delete(media)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/media/{media_id}/attach",
    response_model=MediaRead,
    summary="Attach media to an existing message",
    description=(
        "Attach one of my unattached uploads to a message I sent in the same "
        "conversation. The media must belong to me and the message must not "
        "already have media. The update is broadcast to both participants as a "
        "``message_updated`` WebSocket event (when they have live sockets)."
    ),
    responses=_MEDIA_ERRORS,
)
async def attach_media(
    media_id: str,
    payload: AttachMediaRequest,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MediaRead:
    """Attach an owned, unattached file to an owned message."""
    deps.enforce_rate_limit(request, settings, "media", f"user:{current_user.id}")
    media = media_service.require_media_for_member(
        db, public_id=media_id, user_id=current_user.id
    )
    if media.uploader_id != current_user.id:
        # Do not confirm the existence of another account's unattached upload.
        raise NotFoundError("Media not found.")
    if media.status != "ready":
        raise ConflictError("This media is not available.")
    if media.message_id is not None:
        raise ConflictError("This media is already attached to a message.")

    message = db.get(Message, payload.message_id)
    if message is None or message.conversation_id != media.conversation_id:
        raise NotFoundError("Message not found.")
    if message.sender_id != current_user.id:
        raise ForbiddenError("You can only attach media to your own messages.")
    recipient = chat_service.other_participant(
        db, conversation_id=media.conversation_id, viewer_id=current_user.id
    )
    if is_blocked_between(db, current_user.id, recipient.id):
        raise ForbiddenError("This user is not available.")

    media_service.attach_to_message(db, media=media, message=message)
    rendered = build_message(
        message, sender_username=current_user.username, media=media
    )
    # Commit before notifying anyone: a recipient must never observe an event
    # for an attachment that is not yet durable.
    db.commit()

    manager = getattr(request.app.state, "ws_manager", None)
    if manager is not None:
        member_ids = chat_service.conversation_member_ids(
            db, conversation_id=media.conversation_id
        )
        await manager.broadcast(
            user_ids=member_ids,
            payload=MessageUpdatedEvent(message=rendered).model_dump(mode="json"),
        )

    return build_media(media, uploader_username=current_user.username)
