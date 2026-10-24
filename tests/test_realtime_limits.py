"""Connection management, rate limiting and heartbeat tests."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

import pytest
from fastapi import WebSocketDisconnect

from app.services.realtime import ConnectionManager
from tests._realtime import (
    WS_URL,
    conversation_between,
    custom_client,
    expect_rejection,
    message_frame,
    open_socket,
)


class _FakeSocket:
    """Minimal stand-in for a ``WebSocket`` used by the manager unit tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []
        self.closed_with: tuple[int, str] | None = None

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise RuntimeError("socket is gone")
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


def test_manager_isolates_a_failing_socket() -> None:
    async def scenario() -> None:
        manager = ConnectionManager(
            max_per_user=5,
            max_pending_connections=10,
            max_pending_per_key=10,
        )
        healthy_socket = _FakeSocket()
        broken_socket = _FakeSocket(fail=True)

        healthy = await manager.register(
            healthy_socket, user_id=1, username="alice", token="t"
        )
        broken = await manager.register(
            broken_socket, user_id=2, username="bob", token="t"
        )
        assert healthy is not None and broken is not None

        delivered = await manager.broadcast(
            user_ids=[1, 2], payload={"type": "message"}
        )

        assert delivered == 1
        assert healthy_socket.sent == [{"type": "message"}]
        # The dead connection was removed, the healthy one was not.
        assert manager.connection_count(1) == 1
        assert manager.connection_count(2) == 0

    asyncio.run(scenario())


def test_manager_enforces_the_per_user_cap_without_evicting() -> None:
    async def scenario() -> None:
        manager = ConnectionManager(
            max_per_user=2,
            max_pending_connections=10,
            max_pending_per_key=10,
        )
        first = await manager.register(
            _FakeSocket(), user_id=1, username="alice", token="t"
        )
        second = await manager.register(
            _FakeSocket(), user_id=1, username="alice", token="t"
        )
        rejected = await manager.register(
            _FakeSocket(), user_id=1, username="alice", token="t"
        )

        assert first is not None and second is not None
        assert rejected is None
        assert manager.connection_count(1) == 2

    asyncio.run(scenario())


def test_connection_cap_rejects_only_the_new_socket(client, api) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with ExitStack() as stack:
        sockets = [
            stack.enter_context(open_socket(client, token)) for _ in range(5)
        ]

        with client.websocket_connect(WS_URL) as rejected:
            rejected.send_json({"type": "auth", "token": token})
            expect_rejection(rejected, "too_many_connections", 4429)

        # Every existing device still works.
        for socket in sockets:
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["type"] == "pong"


def test_closing_one_connection_does_not_affect_the_others(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, bob_token) as first, open_socket(
        client, bob_token
    ) as second, open_socket(client, api.login("alice@example.com")) as alice:
        first.close()

        alice.send_json(message_frame(conversation, "still here"))
        assert alice.receive_json()["type"] == "ack"

        event = second.receive_json()
        assert event["type"] == "message"
        assert event["message"]["body"] == "still here"

        second.send_json({"type": "ping"})
        assert second.receive_json()["type"] == "pong"


def test_message_rate_limit_is_enforced_per_account(client, api, test_settings) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_message_rate_limit_attempts=2,
        ws_message_rate_limit_window_seconds=60,
    ) as limited:
        with open_socket(limited, token) as alice:
            for index in range(2):
                alice.send_json(message_frame(conversation, f"message {index}"))
                assert alice.receive_json()["type"] == "ack"

            alice.send_json(message_frame(conversation, "one too many"))
            event = alice.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "rate_limited"
            assert event["retry_after_seconds"] >= 1

            # The budget applies to messages, not to liveness frames.
            alice.send_json({"type": "ping"})
            assert alice.receive_json()["type"] == "pong"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["total"] == 2


def test_heartbeat_pings_and_closes_an_idle_socket(client, api, test_settings) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_heartbeat_interval_seconds=0.02,
        ws_idle_timeout_seconds=0.05,
    ) as short_lived:
        with open_socket(short_lived, token) as alice:
            first = alice.receive_json()
            assert first["type"] == "ping"
            assert first["server_time"]

            # Never answer: the server must give up and close with 1001.
            with pytest.raises(WebSocketDisconnect) as caught:
                while True:
                    alice.receive_json()
            assert caught.value.code == 1001


def test_pong_counts_as_activity(client, api, test_settings) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")

    with custom_client(
        test_settings,
        ws_heartbeat_interval_seconds=0.02,
        ws_idle_timeout_seconds=0.2,
    ) as long_lived:
        with open_socket(long_lived, token) as alice:
            # Answer several server pings; the socket must stay open.
            answered = 0
            while answered < 3:
                event = alice.receive_json()
                assert event["type"] == "ping"
                alice.send_json({"type": "pong"})
                answered += 1

            alice.send_json({"type": "ping"})
            assert alice.receive_json()["type"] == "pong"
