"""Upload, validation, authorization and storage tests for phase 10."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import Conversation, Media, User
from app.core.errors import BadRequestError
from app.services import media as media_service
from app.services.media_validation import prepare_upload, sanitise_filename
from app.services.storage import LocalStorage
from tests._media import (
    CSV_BYTES,
    GIF_BYTES,
    JPEG_BYTES,
    PDF_BYTES,
    PNG_BYTES,
    TEXT_BYTES,
    WEBP_BYTES,
    upload_files,
    upload_one,
)
from tests._realtime import conversation_between, custom_client


def _pair(client, api):
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation_id = conversation_between(client, alice_headers, "bob")
    return conversation_id, alice_headers, bob_headers


def _stored_files(app) -> list[Path]:
    root = Path(app.state.settings.media_storage_path)
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def test_upload_png_returns_metadata_and_serves_authenticated_content(
    client, api, app
) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)

    response = upload_files(
        client,
        alice_headers,
        conversation_id,
        [("holiday photo.png", PNG_BYTES, "image/png")],
    )

    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert len(item["id"]) == 32
    assert item["conversation_id"] == conversation_id
    assert item["uploader_username"] == "alice"
    assert item["filename"] == "holiday photo.png"
    assert item["content_type"] == "image/png"
    assert item["size_bytes"] == len(PNG_BYTES)
    assert (item["width"], item["height"]) == (1, 1)
    assert item["status"] == "ready"
    assert item["message_id"] is None
    assert "storage_key" not in item

    stored = _stored_files(app)
    assert len(stored) == 1
    assert stored[0].read_bytes() == PNG_BYTES

    metadata = client.get(f"/api/v1/media/{item['id']}", headers=bob_headers)
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["uploader_username"] == "alice"

    content = client.get(f"/api/v1/media/{item['id']}/content", headers=bob_headers)
    assert content.status_code == 200
    assert content.content == PNG_BYTES
    assert content.headers["content-type"] == "image/png"
    assert content.headers["content-disposition"].startswith("attachment;")
    assert content.headers["x-content-type-options"] == "nosniff"


def test_upload_allowed_pdf_and_text(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    pdf = upload_one(
        client,
        alice_headers,
        conversation_id,
        filename="report.pdf",
        content=PDF_BYTES,
        content_type="application/pdf",
    )
    assert pdf["content_type"] == "application/pdf"
    assert pdf["width"] is None and pdf["height"] is None

    text = upload_one(
        client,
        alice_headers,
        conversation_id,
        filename="notes.txt",
        content=TEXT_BYTES,
        content_type="text/plain",
    )
    assert text["content_type"] == "text/plain"
    assert text["size_bytes"] == len(TEXT_BYTES)


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "dimensions"),
    [
        ("photo.jpg", JPEG_BYTES, "image/jpeg", (1, 1)),
        ("animation.gif", GIF_BYTES, "image/gif", (1, 1)),
        ("picture.webp", WEBP_BYTES, "image/webp", (1, 1)),
        ("table.csv", CSV_BYTES, "text/csv", (None, None)),
    ],
)
def test_upload_every_supported_format(
    client, api, filename, content, content_type, dimensions
) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    item = upload_one(
        client,
        alice_headers,
        conversation_id,
        filename=filename,
        content=content,
        content_type=content_type,
    )

    assert item["content_type"] == content_type
    assert (item["width"], item["height"]) == dimensions
    assert item["size_bytes"] == len(content)



def test_empty_file_is_rejected(client, api, app) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    response = upload_files(
        client,
        alice_headers,
        conversation_id,
        [("empty.txt", b"", "text/plain")],
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    assert _stored_files(app) == []


def test_oversized_file_is_rejected(client, api, test_settings, app) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    with custom_client(test_settings, media_max_upload_bytes=32) as limited:
        response = upload_files(
            limited,
            alice_headers,
            conversation_id,
            [("big.txt", b"x" * 33, "text/plain")],
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert _stored_files(app) == []


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("malware.exe", b"MZ\x90\x00" + b"\x00" * 32, "application/octet-stream"),
        ("fake.png", b"this is not an image", "image/png"),
        ("mismatch.png", PNG_BYTES, "application/pdf"),
        ("report.pdf", PNG_BYTES, "image/png"),
        ("short.png", b"\x89PNG\r\n\x1a\n" + b"x" * 8, "image/png"),
        ("bad.txt", b"\xff\xfe\x00\x01", "text/plain"),
    ],
)
def test_unsupported_or_mismatched_content_is_rejected(
    client, api, app, filename, content, content_type
) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    response = upload_files(
        client, alice_headers, conversation_id, [(filename, content, content_type)]
    )

    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert _stored_files(app) == []


@pytest.mark.parametrize(
    "filename",
    [
        "../../evil.png",
        "..\\evil.png",
        "/etc/passwd.png",
        "report..png",
    ],
)
def test_unsafe_filenames_are_rejected(client, api, app, filename) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    response = upload_files(
        client, alice_headers, conversation_id, [(filename, PNG_BYTES, "image/png")]
    )

    assert response.status_code == 400, response.text
    assert _stored_files(app) == []


def test_windows_drive_path_is_rejected_by_the_filename_check() -> None:
    """The multipart parser may normalise some names; the validator is strict."""
    with pytest.raises(BadRequestError):
        sanitise_filename("C:\\temp\\evil.png")
    with pytest.raises(BadRequestError):
        sanitise_filename("evil\x00.png")
    with pytest.raises(BadRequestError):
        sanitise_filename("evil.png ")
    with pytest.raises(BadRequestError):
        sanitise_filename("evil.png.")


def test_openapi_documents_the_multipart_upload(client) -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/conversations/{conversation_id}/media"]["post"]

    request_body = operation["requestBody"]
    assert request_body["required"] is True
    media_schema = request_body["content"]["multipart/form-data"]["schema"]
    assert media_schema["properties"]["files"]["type"] == "array"
    assert media_schema["required"] == ["files"]


def test_upload_requires_authentication(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    response = upload_files(client, {}, conversation_id, [("a.png", PNG_BYTES, "image/png")])
    assert response.status_code == 401

    response = upload_files(
        client,
        {"Authorization": "Bearer not-a-token"},
        conversation_id,
        [("a.png", PNG_BYTES, "image/png")],
    )
    assert response.status_code == 401


def test_non_member_cannot_upload_read_metadata_or_download(client, api) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)
    _, carol_headers = api.create_user("carol@example.com")
    item = upload_one(client, alice_headers, conversation_id)

    upload = upload_files(
        client, carol_headers, conversation_id, [("carol.png", PNG_BYTES, "image/png")]
    )
    assert upload.status_code == 404

    assert (
        client.get(f"/api/v1/media/{item['id']}", headers=carol_headers).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/media/{item['id']}/content", headers=carol_headers
        ).status_code
        == 404
    )


def test_upload_count_limit_is_configurable(
    client, api, test_settings, app
) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    with custom_client(test_settings, media_max_files_per_request=1) as limited:
        response = upload_files(
            limited,
            alice_headers,
            conversation_id,
            [("one.png", PNG_BYTES, "image/png"), ("two.png", PNG_BYTES, "image/png")],
        )

    assert response.status_code == 400
    assert _stored_files(app) == []


def test_invalid_batch_stores_nothing(client, api, app) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)

    response = upload_files(
        client,
        alice_headers,
        conversation_id,
        [
            ("good.png", PNG_BYTES, "image/png"),
            ("bad.exe", b"MZ\x00", "application/octet-stream"),
        ],
    )

    assert response.status_code == 415
    assert _stored_files(app) == []


def test_delete_is_uploader_only_and_removes_the_object(client, api, app) -> None:
    conversation_id, alice_headers, bob_headers = _pair(client, api)
    item = upload_one(client, alice_headers, conversation_id)
    assert len(_stored_files(app)) == 1

    forbidden = client.delete(f"/api/v1/media/{item['id']}", headers=bob_headers)
    assert forbidden.status_code == 403

    deleted = client.delete(f"/api/v1/media/{item['id']}", headers=alice_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/media/{item['id']}", headers=alice_headers).status_code == 404
    assert _stored_files(app) == []


def test_missing_stored_object_returns_not_found(client, api, app) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)
    item = upload_one(client, alice_headers, conversation_id)

    stored = _stored_files(app)
    assert len(stored) == 1
    stored[0].unlink()

    response = client.get(
        f"/api/v1/media/{item['id']}/content", headers=alice_headers
    )
    assert response.status_code == 404
    # Metadata is still available, so an operator can see the broken row.
    assert client.get(f"/api/v1/media/{item['id']}", headers=alice_headers).status_code == 200


def test_store_uploads_cleans_up_when_the_database_commit_fails(
    client, api, app, db_session, tmp_path, monkeypatch
) -> None:
    conversation_id, alice_headers, _ = _pair(client, api)
    conversation = db_session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    ).scalar_one()
    uploader = db_session.execute(
        select(User).where(User.username == "alice")
    ).scalar_one()

    prepared = prepare_upload(
        filename="orphan.png",
        declared_content_type="image/png",
        data=PNG_BYTES,
        settings=app.state.settings,
    )
    storage = LocalStorage(tmp_path / "cleanup-media")

    def _fail_commit() -> None:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db_session, "commit", _fail_commit)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        media_service.store_uploads(
            db_session,
            conversation=conversation,
            uploader=uploader,
            uploads=[prepared],
            storage=storage,
        )

    assert [path for path in storage.root.rglob("*") if path.is_file()] == []
    assert (
        db_session.execute(select(func.count()).select_from(Media)).scalar_one() == 0
    )
