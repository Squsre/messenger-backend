"""Security and stability hardening tests for the realtime socket.

These cover the issues fixed in this phase:

* the token is re-validated on an interval even when the client only exchanges
  heartbeat frames (logout, ``token_version`` bump, expiry, deactivation);
* pre-authentication sockets are capped globally and per peer, and every exit
  path releases the slot;
* idempotent retries respect blocks and cannot reuse a key with new content;
* the race in the unique index returns the stored row exactly once.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import func, select

from app.core.security import create_access_token
from app.db.models import Message, User
from app.services import chat as chat_service
from tests._realtime import (
    WS_URL,
    conversation_between,
    custom_client,
    expect_rejection,
    message_frame,
    open_socket,
)
from tests.conftest import deactivate_user, user_id_for

BLOCKS_URL = "/api/v1/blocks"

#: Small, deterministic timing for the timing-sensitive tests. Everything is
#: driven by frames (the server pings every 20 ms), so no test sleeps blindly.
FAST = {
    "ws_heartbeat_interval_seconds": 0.02,
    "ws_idle_timeout_seconds": 5.0,
    "ws_token_recheck_interval_seconds": 0.02,
}


def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    """Wait for a server-side condition to settle (bounded, not a fixed sleep)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("condition was not met before the timeout")


def _pump_until_closed(websocket, *, timeout: float = 8.0) -> tuple[int, list[dict]]:
    """Answer ``ping`` frames until the server closes the socket.

    The server pings every 20 ms in these tests, so a blocking ``receive_json``
    always returns promptly; the deadline only guards against a server that
    never closes.
    """
    frames: list[dict] = []
    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= deadline:
            pytest.fail("the socket was not closed before the timeout")
        try:
            frame = websocket.receive_json()
        except WebSocketDisconnect as exc:
            return exc.code, frames
        frames.append(frame)
        if frame["type"] == "ping":
            websocket.send_json({"type": "pong"})


def _error_codes(frames: list[dict]) -> set[str]:
    return {frame["code"] for frame in frames if frame.get("type") == "error"}


def _expect_pong(websocket, *, timeout: float = 5.0) -> None:
    """Send a ping and wait for the pong, answering heartbeat pings in between."""
    websocket.send_json({"type": "ping"})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = websocket.receive_json()
        except WebSocketDisconnect:
            pytest.fail("the socket closed before answering the ping")
        if frame["type"] == "pong":
            return
        if frame["type"] == "ping":
            websocket.send_json({"type": "pong"})
    pytest.fail("no pong arrived before the timeout")


# --- periodic token/account revalidation -----------------------------------


def test_logout_closes_a_socket_that_sends_no_chat_frames(
    client, api, test_settings
) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(test_settings, **FAST) as ws_client:
        with open_socket(ws_client, token) as alice:
            logout = ws_client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert logout.status_code == 200, logout.text

            code, frames = _pump_until_closed(alice)

    assert code == 4401
    assert "unauthorized" in _error_codes(frames)


def test_logout_everywhere_closes_an_open_socket(client, api, test_settings) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(test_settings, **FAST) as ws_client:
        with open_socket(ws_client, token) as alice:
            response = ws_client.post(
                "/api/v1/auth/logout/all",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200, response.text

            code, _frames = _pump_until_closed(alice)

    # ``token_version`` changed, so every older token is rejected.
    assert code == 4401


def test_deactivating_an_account_closes_an_open_socket(
    client, api, db_session, test_settings
) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(test_settings, **FAST) as ws_client:
        with open_socket(ws_client, token) as alice:
            deactivate_user(db_session, "alice@example.com")

            code, frames = _pump_until_closed(alice)

    assert code == 4403
    assert "forbidden" in _error_codes(frames)


def test_expired_token_closes_a_ping_only_socket(
    client, api, db_session, test_settings
) -> None:
    api.register("alice@example.com")
    uid = user_id_for(db_session, "alice@example.com")
    token, _ = create_access_token(
        uid,
        settings=test_settings,
        expires_delta=timedelta(seconds=2),
    )

    with custom_client(test_settings, **FAST) as ws_client:
        with open_socket(ws_client, token) as alice:
            # No chat frames at all: only heartbeat replies are exchanged.
            code, frames = _pump_until_closed(alice, timeout=10.0)

    assert code == 4401
    assert "unauthorized" in _error_codes(frames)


def test_revoking_one_account_does_not_disturb_another(
    client, api, test_settings
) -> None:
    api.register("alice@example.com")
    api.register("bob@example.com")
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with custom_client(test_settings, **FAST) as ws_client:
        with open_socket(ws_client, alice_token) as alice, open_socket(
            ws_client, bob_token
        ) as bob:
            logout = ws_client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {alice_token}"},
            )
            assert logout.status_code == 200

            code, _frames = _pump_until_closed(alice)
            assert code == 4401

            _expect_pong(bob)


# --- unauthenticated sockets ------------------------------------------------


def test_pending_slot_is_released_after_successful_auth(
    client, api, test_settings
) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_max_pending_connections=1,
        ws_max_pending_connections_per_ip=10,
        ws_auth_timeout_seconds=5.0,
    ) as ws_client:
        manager = ws_client.app.state.ws_manager

        with open_socket(ws_client, token) as first:
            _wait_for(lambda: manager.pending_count() == 0)

            # The single pending slot is free again, so a second handshake can
            # start while the first authenticated socket stays open.
            with open_socket(ws_client, token) as second:
                second.send_json({"type": "ping"})
                assert second.receive_json()["type"] == "pong"

            first.send_json({"type": "ping"})
            assert first.receive_json()["type"] == "pong"


def test_pending_slot_is_released_after_auth_timeout(
    client, api, test_settings
) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_max_pending_connections=1,
        ws_max_pending_connections_per_ip=10,
        ws_auth_timeout_seconds=0.05,
    ) as ws_client:
        manager = ws_client.app.state.ws_manager

        with ws_client.websocket_connect(WS_URL) as silent:
            expect_rejection(silent, "auth_timeout", 4401)

        _wait_for(lambda: manager.pending_count() == 0)
        with open_socket(ws_client, token) as alice:
            alice.send_json({"type": "ping"})
            assert alice.receive_json()["type"] == "pong"


def test_pending_slot_is_released_after_an_invalid_token(
    client, api, test_settings
) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_max_pending_connections=1,
        ws_max_pending_connections_per_ip=10,
    ) as ws_client:
        manager = ws_client.app.state.ws_manager

        with ws_client.websocket_connect(WS_URL) as rejected:
            rejected.send_json({"type": "auth", "token": "definitely-not-valid"})
            expect_rejection(rejected, "unauthorized", 4401)

        _wait_for(lambda: manager.pending_count() == 0)
        with open_socket(ws_client, token) as alice:
            alice.send_json({"type": "ping"})
            assert alice.receive_json()["type"] == "pong"


def test_pending_slot_is_released_after_a_client_disconnect(
    client, api, test_settings
) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_max_pending_connections=1,
        ws_max_pending_connections_per_ip=10,
    ) as ws_client:
        manager = ws_client.app.state.ws_manager

        with ws_client.websocket_connect(WS_URL):
            # Connect and leave without ever authenticating.
            pass

        _wait_for(lambda: manager.pending_count() == 0)
        with open_socket(ws_client, token) as alice:
            alice.send_json({"type": "ping"})
            assert alice.receive_json()["type"] == "pong"


def test_pending_cap_rejects_more_unauthenticated_sockets(
    client, api, test_settings
) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_max_pending_connections=1,
        ws_max_pending_connections_per_ip=10,
    ) as ws_client:
        with ws_client.websocket_connect(WS_URL) as first:
            with ws_client.websocket_connect(WS_URL) as rejected:
                expect_rejection(rejected, "server_busy", 1013)

            # The existing pending socket was not disturbed and can still
            # authenticate normally.
            first.send_json({"type": "auth", "token": token})
            assert first.receive_json()["type"] == "ready"


def test_pending_cap_is_enforced_per_peer(client, api, test_settings) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_max_pending_connections=10,
        ws_max_pending_connections_per_ip=1,
        ws_auth_timeout_seconds=5.0,
    ) as ws_client:
        manager = ws_client.app.state.ws_manager
        peer = "testclient"

        with ws_client.websocket_connect(WS_URL) as first:
            assert manager.pending_count_for(peer) == 1

            with ws_client.websocket_connect(WS_URL) as rejected:
                expect_rejection(rejected, "server_busy", 1013)

            first.send_json({"type": "auth", "token": token})
            assert first.receive_json()["type"] == "ready"

        _wait_for(lambda: manager.pending_count() == 0)

        # The per-peer slot was released with the pending connection.
        with ws_client.websocket_connect(WS_URL):
            pass


# --- idempotency and blocks -------------------------------------------------


def test_retry_after_a_block_is_forbidden(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")
    frame = message_frame(conversation, "hello", client_message_id="block-retry")

    with open_socket(client, bob_token) as bob, open_socket(
        client, api.login("alice@example.com")
    ) as alice:
        alice.send_json(frame)
        first = alice.receive_json()
        assert first["type"] == "ack"
        assert first["duplicate"] is False
        assert bob.receive_json()["message"]["id"] == first["message"]["id"]

        blocked = client.post(
            BLOCKS_URL, json={"username": "bob"}, headers=alice_headers
        )
        assert blocked.status_code == 201

        # The retry must not be served as a successful replay while blocked.
        alice.send_json(frame)
        error = alice.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "forbidden"

        # Nothing was broadcast, and the socket is still usable for pings.
        bob.send_json({"type": "ping"})
        assert bob.receive_json()["type"] == "pong"
        alice.send_json({"type": "ping"})
        assert alice.receive_json()["type"] == "pong"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["total"] == 1


def test_reusing_a_key_with_a_different_body_is_a_conflict(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("alice@example.com")) as alice:
        alice.send_json(
            message_frame(conversation, "original", client_message_id="same-key")
        )
        ack = alice.receive_json()
        assert ack["type"] == "ack"
        assert ack["duplicate"] is False

        alice.send_json(
            message_frame(conversation, "different", client_message_id="same-key")
        )
        error = alice.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "conflict"

        alice.send_json({"type": "ping"})
        assert alice.receive_json()["type"] == "pong"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["total"] == 1
    assert history["items"][0]["body"] == "original"


def test_concurrent_duplicate_key_returns_the_stored_message(
    client, api, database, monkeypatch
) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = conversation_between(client, alice_headers, "bob")

    with database.session() as db:
        alice = db.execute(
            select(User).where(User.username == "alice")
        ).scalar_one()
        conversation = chat_service.require_membership(
            db, conversation_id=conversation_id, user_id=alice.id
        )
        first, created = chat_service.send_message(
            db,
            conversation=conversation,
            sender=alice,
            body="only once",
            client_message_id="race-key",
        )
    assert created is True

    # Simulate the loser of a race: its pre-insert lookup runs before the
    # winner commits, so it misses the row and hits the unique index instead.
    original_lookup = chat_service._message_by_client_id
    calls = {"count": 0}

    def miss_the_first_lookup(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return select(Message).where(Message.id == -1)
        return original_lookup(*args, **kwargs)

    monkeypatch.setattr(
        chat_service, "_message_by_client_id", miss_the_first_lookup
    )

    with database.session() as db:
        alice = db.execute(
            select(User).where(User.username == "alice")
        ).scalar_one()
        conversation = chat_service.require_membership(
            db, conversation_id=conversation_id, user_id=alice.id
        )
        replay, created = chat_service.send_message(
            db,
            conversation=conversation,
            sender=alice,
            body="only once",
            client_message_id="race-key",
        )
        assert created is False
        assert replay.id == first.id

    # Both the miss and the recovery lookup happened, and only one row exists.
    assert calls["count"] >= 2
    with database.session() as db:
        total = db.execute(select(func.count()).select_from(Message)).scalar_one()
    assert total == 1
