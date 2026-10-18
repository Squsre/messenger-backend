"""Tests for application startup, docs and wiring."""

from __future__ import annotations


def test_docs_are_available(client) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_openapi_schema_describes_health(client) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Messenger API"
    assert schema["info"]["version"] == "0.1.0"
    assert "/api/v1/health" in schema["paths"]


def test_root_banner(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["docs_url"] == "/docs"
    assert body["health_url"] == "/api/v1/health"


def test_unknown_route_returns_404(client) -> None:
    assert client.get("/api/v1/does-not-exist").status_code == 404
