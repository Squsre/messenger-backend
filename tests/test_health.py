"""Tests for the health endpoint."""

from __future__ import annotations


def test_health_returns_ok(client) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Messenger API"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "test"


def test_health_is_under_versioned_prefix(client) -> None:
    """The unversioned path must not exist."""
    assert client.get("/health").status_code == 404


def test_health_content_type_is_json(client) -> None:
    response = client.get("/api/v1/health")
    assert response.headers["content-type"].startswith("application/json")
