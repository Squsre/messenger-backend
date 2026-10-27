"""Storage configuration and key-generation tests (no network required)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.storage import new_storage_key

BASE = {
    "app_env": "test",
    "database_url": "postgresql+psycopg://test:test@127.0.0.1:5432/test",
    "jwt_secret_key": "unit-test-secret-0123456789abcdefghijklmnopqrstuvwxyz",
}


def _minio_settings(**overrides):
    values = {
        **BASE,
        "media_storage_backend": "minio",
        "minio_access_key": "messenger-app",
        "minio_secret_key": "unit-test-minio-secret",
        "minio_endpoint": "127.0.0.1:9000",
        "minio_public_endpoint": "127.0.0.1:9000",
        "minio_bucket": "messenger-media",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_new_storage_key_is_random_and_uses_server_extension() -> None:
    first = new_storage_key("png")
    second = new_storage_key("png")

    assert first != second
    assert first.endswith(".png")
    assert len(first.split(".")[0]) == 32
    assert first.replace(".", "").isalnum()


def test_new_storage_key_rejects_unsafe_extension() -> None:
    with pytest.raises(ValueError):
        new_storage_key("../png")
    with pytest.raises(ValueError):
        new_storage_key("")


def test_minio_requires_application_credentials() -> None:
    with pytest.raises(ValidationError):
        _minio_settings(minio_access_key="")
    with pytest.raises(ValidationError):
        _minio_settings(minio_secret_key="short")
    with pytest.raises(ValidationError):
        _minio_settings(
            app_env="production",
            email_backend="disabled",
            minio_secret_key="dev-only-change-me",
        )


def test_minio_endpoint_must_be_bare_host_and_port() -> None:
    with pytest.raises(ValidationError):
        _minio_settings(minio_endpoint="http://minio:9000")
    with pytest.raises(ValidationError):
        _minio_settings(minio_endpoint="minio")
    with pytest.raises(ValidationError):
        _minio_settings(minio_endpoint="minio:9000/path")
    with pytest.raises(ValidationError):
        _minio_settings(minio_endpoint="minio host:9000")
    with pytest.raises(ValidationError):
        _minio_settings(minio_endpoint="minio:0")


def test_minio_bucket_and_prefix_are_validated_and_normalised() -> None:
    with pytest.raises(ValidationError):
        _minio_settings(minio_bucket="Bad Bucket")
    with pytest.raises(ValidationError):
        _minio_settings(minio_key_prefix="team/../prod")

    settings = _minio_settings(minio_key_prefix="team/test")
    assert settings.minio_key_prefix == "team/test/"


def test_presigned_url_lifetime_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _minio_settings(minio_presigned_url_expires_seconds=29)
    with pytest.raises(ValidationError):
        _minio_settings(minio_presigned_url_expires_seconds=3601)

    settings = _minio_settings(minio_presigned_url_expires_seconds=60)
    assert settings.minio_presigned_url_expires_seconds == 60
