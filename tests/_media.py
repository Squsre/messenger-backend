"""Shared fixtures for media tests (not collected as a test module)."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

#: A real 1x1 PNG (IHDR width/height = 1). The server only parses the header,
#: so this is enough to exercise the allow-list and dimension extraction.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"%%EOF\n"
)

TEXT_BYTES = b"hello, this is a plain text file\n"
CSV_BYTES = b"name,value\nalice,1\nbob,2\n"

#: Minimal headers for the other allowed image types. The validator only reads
#: bounded header fields, so these exercise the real format branches without
#: pulling in an image library or decoding pixels.
JPEG_BYTES = (
    b"\xff\xd8"  # SOI
    + b"\xff\xe0\x00\x10"
    + b"JFIF\x00"
    + b"\x00" * 9  # APP0, 16-byte segment
    + b"\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03"
    + b"\x00" * 9  # SOF0, 1x1
    + b"\xff\xd9"  # EOI
)

GIF_BYTES = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"

WEBP_BYTES = (
    b"RIFF"
    + (22).to_bytes(4, "little")
    + b"WEBP"
    + b"VP8X"
    + (10).to_bytes(4, "little")
    + b"\x00\x00\x00\x00"
    + b"\x00\x00\x00"  # canvas width - 1
    + b"\x00\x00\x00"  # canvas height - 1
)



def upload_files(
    client: TestClient,
    headers: dict[str, str],
    conversation_id: int,
    files: list[tuple[str, bytes, str]],
):
    """POST a multipart upload using the documented ``files`` field."""
    return client.post(
        f"/api/v1/conversations/{conversation_id}/media",
        headers=headers,
        files=[
            ("files", (filename, content, content_type))
            for filename, content, content_type in files
        ],
    )


def upload_one(
    client: TestClient,
    headers: dict[str, str],
    conversation_id: int,
    *,
    filename: str = "photo.png",
    content: bytes = PNG_BYTES,
    content_type: str = "image/png",
) -> dict:
    response = upload_files(
        client, headers, conversation_id, [(filename, content, content_type)]
    )
    assert response.status_code == 201, response.text
    return response.json()["items"][0]
