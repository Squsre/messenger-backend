"""Shared helpers for the realtime test modules.

Imported by ``tests/test_realtime_*.py``; not collected as a test module because
its name does not start with ``test_``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.core.config import Settings
from app.main import create_app

WS_URL = "/api/v1/ws"


def conversation_between(client: TestClient, headers: dict, username: str = "bob") -> int:
    response = client.post(
        "/api/v1/conversations", json={"username": username}, headers=headers
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


@contextmanager
def open_socket(client: TestClient, token: str) -> Iterator[WebSocketTestSession]:
    """Connect and authenticate, yielding the live session."""
    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        ready = websocket.receive_json()
        assert ready["type"] == "ready", ready
        yield websocket


def expect_rejection(
    websocket: WebSocketTestSession, error_code: str, close_code: int
) -> dict:
    """Assert an ``error`` event followed by a close with ``close_code``."""
    event = websocket.receive_json()
    assert event["type"] == "error", event
    assert event["code"] == error_code, event
    with pytest.raises(WebSocketDisconnect) as caught:
        websocket.receive_json()
    assert caught.value.code == close_code
    return event


@contextmanager
def custom_client(test_settings: Settings, **overrides: object) -> Iterator[TestClient]:
    """A second app on the same database with overridden settings.

    Used to make timeouts, caps and rate limits tiny without slowing the rest of
    the suite. The schema already exists (the ``app`` fixture created it).
    """
    settings = test_settings.model_copy(update=overrides)
    application = create_app(settings=settings)
    with TestClient(application) as client:
        yield client


def message_frame(
    conversation_id: int,
    body: str,
    client_message_id: str | None = None,
) -> dict:
    frame: dict = {"type": "message", "conversation_id": conversation_id, "body": body}
    if client_message_id is not None:
        frame["client_message_id"] = client_message_id
    return frame
