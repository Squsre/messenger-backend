"""Realtime handshake and authentication tests."""

from __future__ import annotations

from datetime import timedelta

from app.core.security import create_access_token
from tests._realtime import (
    WS_URL,
    conversation_between,
    custom_client,
    expect_rejection,
    open_socket,
)
from tests.conftest import deactivate_user, user_id_for


def test_valid_token_opens_the_socket(client, api) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")
    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        ready = websocket.receive_json()

        assert ready["type"] == "ready"
        assert ready["user"]["username"] == "alice"
        assert "email" not in ready["user"]
        assert ready["connection_id"]
        assert ready["server_time"]


def test_ready_lists_the_callers_conversations(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    api.register("carol@example.com")
    first = conversation_between(client, alice_headers, "bob")
    second = conversation_between(client, alice_headers, "carol")

    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth", "token": api.login("alice@example.com")})
        ready = websocket.receive_json()

    assert ready["type"] == "ready"
    assert sorted(ready["conversations"]) == sorted([first, second])


def test_malformed_token_is_rejected(client, api) -> None:
    api.register("alice@example.com")
    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth", "token": "not-a-real-token"})
        expect_rejection(websocket, "unauthorized", 4401)


def test_expired_token_is_rejected(client, api, db_session, test_settings) -> None:
    api.register("alice@example.com")
    expired, _ = create_access_token(
        user_id_for(db_session, "alice@example.com"),
        settings=test_settings,
        expires_delta=timedelta(minutes=-1),
    )
    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth", "token": expired})
        expect_rejection(websocket, "unauthorized", 4401)


def test_revoked_token_is_rejected(client, api) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")
    logout = client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert logout.status_code == 200, logout.text

    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        expect_rejection(websocket, "unauthorized", 4401)


def test_deactivated_account_is_rejected(client, api, db_session) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")
    deactivate_user(db_session, "alice@example.com")

    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        event = expect_rejection(websocket, "forbidden", 4403)
        assert event["message"] == "This account is disabled."


def test_first_frame_must_be_auth(client, api) -> None:
    api.register("alice@example.com")
    client.post("/api/v1/conversations", json={"username": "alice"})

    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "ping"})
        expect_rejection(websocket, "auth_required", 4401)


def test_malformed_auth_frame_is_rejected(client, api) -> None:
    api.register("alice@example.com")
    with client.websocket_connect(WS_URL) as websocket:
        websocket.send_json({"type": "auth"})
        expect_rejection(websocket, "invalid_payload", 4401)


def test_auth_timeout_closes_the_socket(client, api, test_settings) -> None:
    api.register("alice@example.com")
    with custom_client(test_settings, ws_auth_timeout_seconds=0.05) as slow_client:
        with slow_client.websocket_connect(WS_URL) as websocket:
            expect_rejection(websocket, "auth_timeout", 4401)


def test_authenticating_twice_is_refused_but_keeps_the_socket(client, api) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")
    with open_socket(client, token) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        event = websocket.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "already_authenticated"

        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"
