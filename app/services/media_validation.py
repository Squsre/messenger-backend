"""Filename and content validation for uploaded media.

The client's ``Content-Type`` and filename are **claims**, never trusted on
their own. An upload is accepted only when:

1. the filename is a safe display label (no separators, control characters or
   traversal fragments),
2. its extension is on the small allow-list below,
3. the declared MIME type (when present) agrees with the type detected from the
   bytes, and
4. the bytes match the expected format.

Images are never decoded here. Dimensions are read from a bounded header parse,
which keeps the server safe from decompression bombs and expensive image work;
thumbnails/transcoding are deliberately a later concern. The allow-list is
intentionally small: common web images, PDF and plain text/CSV. Executables,
archives, HTML and SVG are refused.
"""

from __future__ import annotations

import hashlib
import struct
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.errors import (
    BadRequestError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)

#: Extension -> the canonical type its bytes must resolve to.
_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
}

#: MIME values accepted for each canonical type. Browsers occasionally send
#: ``image/jpg``; an unspecified type is tolerated because the bytes are still
#: checked. Anything else is a mismatch.
_ACCEPTED_DECLARED_TYPES: dict[str, frozenset[str]] = {
    "image/jpeg": frozenset({"image/jpeg", "image/jpg", "image/pjpeg"}),
    "image/png": frozenset({"image/png"}),
    "image/gif": frozenset({"image/gif"}),
    "image/webp": frozenset({"image/webp"}),
    "application/pdf": frozenset({"application/pdf"}),
    "text/plain": frozenset({"text/plain"}),
    "text/csv": frozenset({"text/csv", "application/csv"}),
}

_UNSPECIFIED_DECLARED_TYPES = frozenset(
    {"", "application/octet-stream", "binary/octet-stream"}
)

#: Human-readable list used in error messages (never echoes the filename).
ALLOWED_MEDIA_DESCRIPTION = "JPEG, PNG, GIF, WebP, PDF, TXT or CSV"


@dataclass(frozen=True)
class DetectedContent:
    """The type actually inferred from the bytes."""

    content_type: str
    extension: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class PreparedUpload:
    """A validated upload ready to be written to storage and the database."""

    original_filename: str
    content_type: str
    extension: str
    size_bytes: int
    width: int | None
    height: int | None
    data: bytes
    checksum_sha256: str = ""


def sanitise_filename(filename: str | None) -> str:
    """Return a safe display filename or raise ``400``.

    The name is only ever stored for display; storage keys are generated
    separately. Rejecting traversal fragments anyway prevents surprising output
    and makes the contract explicit.
    """
    if filename is None:
        raise BadRequestError("A filename is required.")
    if filename.endswith((" ", ".")):
        raise BadRequestError("Invalid filename.")
    name = filename.strip()
    if not name:
        raise BadRequestError("A filename is required.")
    if len(name) > 255:
        raise BadRequestError("Filename is too long.")
    if name in {".", ".."} or ".." in name:
        raise BadRequestError("Invalid filename.")
    if any(separator in name for separator in ("/", "\\", ":", "\x00")):
        raise BadRequestError("Invalid filename.")
    if any(
        unicodedata.category(character) in {"Cc", "Cf"} for character in name
    ):
        raise BadRequestError("Invalid filename.")
    return name


def _normalise_declared_type(value: str | None) -> str:
    if not value:
        return ""
    return value.split(";", 1)[0].strip().lower()


def _read_dimensions(content_type: str, data: bytes) -> tuple[int, int] | None:
    """Read dimensions from a bounded header parse, or return ``None``."""
    if content_type == "image/png":
        if len(data) < 24 or data[12:16] != b"IHDR":
            return None
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)

    if content_type == "image/gif":
        if len(data) < 10:
            return None
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return width, height

    if content_type == "image/jpeg":
        return _jpeg_dimensions(data)

    if content_type == "image/webp":
        return _webp_dimensions(data)

    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Find a Start-Of-Frame segment without decoding pixel data."""
    index = 2
    while index + 3 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            return None
        marker = data[index]
        index += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if length < 7:
                return None
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        if marker == 0xDA:  # Start of scan: no frame header was found.
            return None
        index += length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8L" and data[20] == 0x2F:
        first, second, third, fourth = data[21:25]
        width = 1 + (((second & 0x3F) << 8) | first)
        height = 1 + (
            ((fourth & 0x0F) << 10) | (third << 2) | ((second & 0xC0) >> 6)
        )
        return width, height
    if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    return None


def _detect(
    data: bytes, *, expected_type: str, settings: Settings
) -> DetectedContent:
    """Infer the real type and dimensions, or raise ``415``."""
    detected_type: str | None = None
    width: int | None = None
    height: int | None = None

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected_type = "image/png"
        dimensions = _read_dimensions(detected_type, data)
    elif data.startswith(b"\xff\xd8\xff"):
        detected_type = "image/jpeg"
        dimensions = _read_dimensions(detected_type, data)
    elif data.startswith((b"GIF87a", b"GIF89a")):
        detected_type = "image/gif"
        dimensions = _read_dimensions(detected_type, data)
    elif data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        detected_type = "image/webp"
        dimensions = _read_dimensions(detected_type, data)
    elif data.startswith(b"%PDF-"):
        detected_type = "application/pdf"
        dimensions = None
    else:
        dimensions = None

    if detected_type is None:
        # Text is the only format without a magic signature. It must be valid
        # UTF-8 and free of NUL bytes; the extension/declared type must also be
        # a text type.
        if expected_type not in {"text/plain", "text/csv"}:
            raise UnsupportedMediaTypeError(
                f"Unsupported file content. Allowed formats: {ALLOWED_MEDIA_DESCRIPTION}."
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedMediaTypeError(
                f"Unsupported file content. Allowed formats: {ALLOWED_MEDIA_DESCRIPTION}."
            ) from exc
        if "\x00" in text:
            raise UnsupportedMediaTypeError(
                f"Unsupported file content. Allowed formats: {ALLOWED_MEDIA_DESCRIPTION}."
            )
        detected_type = expected_type
        dimensions = None

    if detected_type != expected_type:
        raise UnsupportedMediaTypeError(
            f"Unsupported file content. Allowed formats: {ALLOWED_MEDIA_DESCRIPTION}."
        )

    extension = next(
        (candidate for candidate, content_type in _ALLOWED_EXTENSIONS.items() if content_type == detected_type),
        None,
    )
    if extension is None:  # pragma: no cover - guarded by the extension table
        raise UnsupportedMediaTypeError("Unsupported file type.")

    if detected_type.startswith("image/"):
        if dimensions is None:
            raise UnsupportedMediaTypeError("The image file appears to be malformed.")
        width, height = dimensions
        if not (1 <= width <= settings.media_max_image_dimension) or not (
            1 <= height <= settings.media_max_image_dimension
        ):
            raise UnsupportedMediaTypeError("The image dimensions are not supported.")

    return DetectedContent(
        content_type=detected_type,
        extension=extension,
        width=width,
        height=height,
    )


def prepare_upload(
    *,
    filename: str | None,
    declared_content_type: str | None,
    data: bytes,
    settings: Settings,
) -> PreparedUpload:
    """Validate one upload and return the values to persist."""
    safe_name = sanitise_filename(filename)
    if not data:
        raise BadRequestError("The uploaded file is empty.")
    if len(data) > settings.media_max_upload_bytes:
        raise PayloadTooLargeError(
            f"The uploaded file exceeds the {settings.media_max_upload_bytes}-byte limit."
        )

    suffix = Path(safe_name).suffix.lower()
    expected_type = _ALLOWED_EXTENSIONS.get(suffix)
    if expected_type is None:
        raise UnsupportedMediaTypeError(
            f"Unsupported file type. Allowed formats: {ALLOWED_MEDIA_DESCRIPTION}."
        )

    declared = _normalise_declared_type(declared_content_type)
    if declared not in _UNSPECIFIED_DECLARED_TYPES:
        accepted = _ACCEPTED_DECLARED_TYPES[expected_type]
        if declared not in accepted:
            raise UnsupportedMediaTypeError(
                "The declared content type does not match the file."
            )

    detected = _detect(
        data,
        expected_type=expected_type,
        settings=settings,
    )

    return PreparedUpload(
        original_filename=safe_name,
        content_type=detected.content_type,
        extension=detected.extension,
        size_bytes=len(data),
        width=detected.width,
        height=detected.height,
        data=data,
        checksum_sha256=hashlib.sha256(data).hexdigest(),
    )
