"""API representations for uploaded media."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Media

MediaStatus = Literal["pending", "ready", "failed"]


class MediaRead(BaseModel):
    """Metadata for one stored file.

    The storage key is intentionally absent: clients use the opaque ``id`` with
    the authenticated content endpoint and never learn a path.
    """

    id: str = Field(description="Opaque, unguessable media id.")
    conversation_id: int
    uploader_username: str = Field(description="Account that uploaded the file.")
    filename: str = Field(description="Safe display name of the original file.")
    content_type: str = Field(description="Type detected from the file content.")
    size_bytes: int
    width: int | None = Field(default=None, description="Image width, if applicable.")
    height: int | None = Field(default=None, description="Image height, if applicable.")
    status: MediaStatus = Field(
        description="Processing state; synchronous uploads are ready immediately."
    )
    checksum_sha256: str | None = Field(
        default=None,
        description="SHA-256 of the accepted bytes, when recorded.",
    )
    message_id: int | None = Field(
        default=None,
        description="Message this file is attached to, or null while unattached.",
    )
    created_at: datetime


class MediaBatchRead(BaseModel):
    """Result of a multi-file upload (1..MEDIA_MAX_FILES_PER_REQUEST files)."""

    items: list[MediaRead]


class MediaAccessUrlRead(BaseModel):
    """A short-lived direct download URL issued after authorization."""

    media_id: str = Field(description="Opaque media id the URL belongs to.")
    url: str = Field(description="Presigned object-storage URL; do not log or cache.")
    expires_in_seconds: int = Field(
        ge=1,
        description="Seconds until the URL expires.",
    )


class AttachMediaRequest(BaseModel):
    """Attach an already-uploaded file to one of my existing messages."""

    model_config = ConfigDict(extra="forbid")

    message_id: int = Field(
        ge=1,
        description="A message I sent in the same conversation as the media.",
        examples=[42],
    )


def build_media(media: Media, *, uploader_username: str) -> MediaRead:
    """Convert a stored media row to its API representation."""
    return MediaRead(
        id=media.public_id,
        conversation_id=media.conversation_id,
        uploader_username=uploader_username,
        filename=media.original_filename,
        content_type=media.content_type,
        size_bytes=media.size_bytes,
        width=media.width,
        height=media.height,
        status=media.status,  # type: ignore[arg-type]
        checksum_sha256=media.checksum_sha256,
        message_id=media.message_id,
        created_at=media.created_at,
    )
