"""MinIO integration tests (upload, authorization, URLs, cleanup and failures).

The tests are skipped when the local MinIO service is not reachable or the
bucket has not been initialized. They never use the development key prefix:
every test gets a unique ``minio_key_prefix`` and the fixture removes only that
prefix.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.services.storage import MinioStorage, StorageError
from tests._media import PNG_BYTES, upload_files, upload_one
from tests._realtime import conversation_between
from tests.conftest import ApiHelper


class _FailingStorage:
    """Storage backend that fails every write, for the 503 mapping test."""

    backend_name = "minio"

    def save(self, *_args, **_kwargs):
        raise StorageError("simulated storage outage")

    def delete(self, *_args, **_kwargs):
        raise StorageError("simulated storage outage")

    def iter_objects(self, *_args, **_kwargs):
        return []

    def health(self) -> bool:
        return False

    def close(self) -> None:
        return None


@pytest.fixture()
def minio_settings(test_settings: Settings, app) -> Settings:
    """Settings that use MinIO with a unique, test-only object prefix.

    ``app`` is requested first so the isolated PostgreSQL schema already exists.
    The storage probe is read-only and skips the test (rather than pretending it
    passed) when the service/bucket is unavailable.
    """
    prefix = f"test-minio-{uuid.uuid4().hex}/"
    settings = test_settings.model_copy(
        update={
            "media_storage_backend": "minio",
            "minio_key_prefix": prefix,
            "minio_auto_create_bucket": False,
            "minio_presigned_url_expires_seconds": 60,
        }
    )
    storage = None
    try:
        storage = MinioStorage(settings)
        if not storage.health():
            pytest.skip(
                "MinIO is not reachable or the configured bucket is missing; "
                "start it with 'docker compose up -d minio minio-init'."
            )
    except Exception as exc:  # noqa: BLE001 - unavailable optional service
        pytest.skip(f"MinIO integration is unavailable: {type(exc).__name__}")
    finally:
        if storage is not None:
            storage.close()
    return settings


@pytest.fixture()
def minio_app(minio_settings: Settings):
    application = create_app(settings=minio_settings)
    Base.metadata.create_all(bind=application.state.database.engine)
    yield application

    storage = application.state.storage
    try:
        for item in list(storage.iter_objects()):
            storage.delete(item.key)
    except StorageError:
        # Do not hide a test failure if MinIO disappeared mid-test; cleanup is
        # best effort and the prefix is unique.
        pass
    finally:
        storage.close()
        application.state.redis_manager.close()
        application.state.database.dispose()


@pytest.fixture()
def minio_client(minio_app) -> Iterator[TestClient]:
    with TestClient(minio_app) as client:
        yield client


@pytest.fixture()
def minio_api(minio_client) -> ApiHelper:
    return ApiHelper(minio_client)


def _pair(client, api):
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation_id = conversation_between(client, alice_headers, "bob")
    return conversation_id, alice_headers, bob_headers


def _send(client, headers, conversation_id, payload):
    return client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json=payload,
    )


def _history(client, headers, conversation_id):
    response = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_minio_upload_download_attachment_and_delete(
    minio_client, minio_api, minio_app, db_session
) -> None:
    conversation_id, alice_headers, bob_headers = _pair(minio_client, minio_api)

    response = upload_files(
        minio_client,
        alice_headers,
        conversation_id,
        [("holiday.png", PNG_BYTES, "image/png")],
    )
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["checksum_sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()

    from sqlalchemy import select

    from app.db.models import Media

    row = db_session.execute(
        select(Media).where(Media.public_id == item["id"])
    ).scalar_one()
    assert row.storage_backend == "minio"
    assert row.checksum_sha256 == hashlib.sha256(PNG_BYTES).hexdigest()
    assert row.etag

    content = minio_client.get(
        f"/api/v1/media/{item['id']}/content", headers=bob_headers
    )
    assert content.status_code == 200, content.text
    assert content.content == PNG_BYTES

    sent = _send(
        minio_client,
        alice_headers,
        conversation_id,
        {"body": "attached", "media_id": item["id"]},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["media"]["id"] == item["id"]
    assert _history(minio_client, bob_headers, conversation_id)["items"][0]["media"][
        "id"
    ] == item["id"]

    # Attached media cannot be deleted; create a second unattached upload and
    # verify that both the object and the metadata row disappear.
    unattached = upload_one(minio_client, alice_headers, conversation_id)
    unattached_row = db_session.execute(
        select(Media).where(Media.public_id == unattached["id"])
    ).scalar_one()
    unattached_key = unattached_row.storage_key
    deleted = minio_client.delete(
        f"/api/v1/media/{unattached['id']}", headers=alice_headers
    )
    assert deleted.status_code == 204, deleted.text
    assert (
        minio_client.get(
            f"/api/v1/media/{unattached['id']}", headers=alice_headers
        ).status_code
        == 404
    )
    assert minio_app.state.storage.exists(unattached_key) is False


def test_minio_presigned_url_is_authorized_and_short_lived(
    minio_client, minio_api, minio_app
) -> None:
    conversation_id, alice_headers, bob_headers = _pair(minio_client, minio_api)
    _, carol_headers = minio_api.create_user("carol@example.com")
    item = upload_one(minio_client, alice_headers, conversation_id)

    forbidden = minio_client.get(
        f"/api/v1/media/{item['id']}/url", headers=carol_headers
    )
    assert forbidden.status_code == 404

    issued = minio_client.get(
        f"/api/v1/media/{item['id']}/url", headers=bob_headers
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["media_id"] == item["id"]
    assert body["expires_in_seconds"] == 60
    assert body["url"].startswith("http")
    assert issued.headers["cache-control"].startswith("private")

    downloaded = httpx.get(body["url"], timeout=10)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == PNG_BYTES


def test_minio_invalid_and_oversized_uploads_store_nothing(
    minio_client, minio_api, minio_settings, minio_app
) -> None:
    conversation_id, alice_headers, _ = _pair(minio_client, minio_api)

    invalid = upload_files(
        minio_client,
        alice_headers,
        conversation_id,
        [("malware.exe", b"MZ\x00", "application/octet-stream")],
    )
    assert invalid.status_code == 415

    with TestClient(
        create_app(
            settings=minio_settings.model_copy(
                update={"media_max_upload_bytes": 32}
            )
        )
    ) as limited:
        oversized = upload_files(
            limited,
            alice_headers,
            conversation_id,
            [("big.txt", b"x" * 33, "text/plain")],
        )
    assert oversized.status_code == 413

    assert list(minio_app.state.storage.iter_objects()) == []


def test_minio_storage_failure_returns_503_and_rolls_back(
    minio_client, minio_api, minio_app, db_session
) -> None:
    conversation_id, alice_headers, _ = _pair(minio_client, minio_api)
    original_storage = minio_app.state.storage
    minio_app.state.storage = _FailingStorage()

    try:
        response = upload_files(
            minio_client,
            alice_headers,
            conversation_id,
            [("photo.png", PNG_BYTES, "image/png")],
        )

        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "service_unavailable"

        from sqlalchemy import func, select

        from app.db.models import Media

        assert (
            db_session.execute(select(func.count()).select_from(Media)).scalar_one()
            == 0
        )
    finally:
        original_storage.close()


def test_minio_readiness_reports_storage(minio_client) -> None:
    response = minio_client.get("/api/v1/health/ready")

    assert response.status_code == 200, response.text
    assert response.json()["checks"] == {
        "database": "ok",
        "redis": "ok",
        "storage": "ok",
    }
