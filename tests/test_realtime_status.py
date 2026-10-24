"""WebSocket tests for delivery/read events, typing indicators and status privacy.

These tests drive the socket directly. The persisted representation and the HTTP
read endpoint are covered by ``tests/test_message_status.py``.
"""

from __future__ import annotations

from starlette.testclient import WebSocketTestSession

from tests._realtime import (
    conversation_between,
    custom_client,
    message_frame,
    open_socket,
)

CONVERSATIONS_URL = "/api/v1/conversations"
BLOCKS_URL = "/api/v1/blocks"


def _expect_pong(
    websocket: WebSocketTestSession, *, forbidden: tuple[str, ...] = ()
) -> None:
    """Synchronise with the server and prove no forbidden event is queued.

    A ``ping`` is answered only after the server has processed every earlier
    frame (per-connection sends are serialised), so anything received before the
    ``pong`` is an event that should not have been emitted.
    """
    websocket.send_json({"type": "ping"})
    while True:
        frame = websocket.receive_json()
        if frame["type"] == "pong":
            return
        assert frame["type"] not in forbidden, frame
        if frame["type"] == "ping":
            websocket.send_json({"type": "pong"})


# --- delivery ---------------------------------------------------------------


def test_delivered_event_reaches_the_senders_other_devices(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers)
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with (
        open_socket(client, alice_token) as alice_one,
        open_socket(client, alice_token) as alice_two,
        open_socket(client, bob_token) as bob,
    ):
        alice_one.send_json(message_frame(conversation, "hi", "dev-1"))
        ack = alice_one.receive_json()
        assert ack["type"] == "ack"
        assert ack["duplicate"] is False
        message = ack["message"]
        assert message["delivered_at"] is not None
        assert message["read_at"] is None

        # The sender's other device sees the message and then the delivery.
        own_copy = alice_two.receive_json()
        assert own_copy["type"] == "message"
        assert own_copy["message"]["id"] == message["id"]
        delivered = alice_two.receive_json()
        assert delivered["type"] == "delivered"
        assert delivered["conversation_id"] == conversation
        assert delivered["message_id"] == message["id"]
        assert delivered["delivered_at"] is not None

        received = bob.receive_json()
        assert received["type"] == "message"
        assert received["message"]["id"] == message["id"]
        assert received["message"]["delivered_at"] is not None


def test_duplicate_retry_does_not_emit_events_again(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers)
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with (
        open_socket(client, alice_token) as alice_one,
        open_socket(client, alice_token) as alice_two,
        open_socket(client, bob_token) as bob,
    ):
        alice_one.send_json(message_frame(conversation, "hi", "same-key"))
        message_id = alice_one.receive_json()["message"]["id"]
        assert alice_two.receive_json()["type"] == "message"
        assert alice_two.receive_json()["type"] == "delivered"
        assert bob.receive_json()["message"]["id"] == message_id

        alice_one.send_json(message_frame(conversation, "hi", "same-key"))
        retry = alice_one.receive_json()
        assert retry["type"] == "ack"
        assert retry["duplicate"] is True
        assert retry["message"]["id"] == message_id

        _expect_pong(alice_two, forbidden=("message", "delivered", "read"))
        _expect_pong(bob, forbidden=("message", "delivered", "read"))

        history = client.get(
            f"{CONVERSATIONS_URL}/{conversation}/messages", headers=alice_headers
        ).json()
        assert history["total"] == 1


# --- read receipts ----------------------------------------------------------


def test_read_frame_updates_both_participants(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = conversation_between(client, alice_headers)
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with (
        open_socket(client, alice_token) as alice,
        open_socket(client, bob_token) as bob,
    ):
        alice.send_json(message_frame(conversation, "read me"))
        message = alice.receive_json()["message"]
        assert bob.receive_json()["message"]["id"] == message["id"]

        bob.send_json(
            {
                "type": "read",
                "conversation_id": conversation,
                "last_message_id": message["id"],
            }
        )

        for socket in (alice, bob):
            event = socket.receive_json()
            assert event["type"] == "read"
            assert event["conversation_id"] == conversation
            assert event["message_id"] == message["id"]
            assert event["reader_username"] == "bob"
            assert event["read_at"] is not None

    history = client.get(
        f"{CONVERSATIONS_URL}/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["items"][0]["read_at"] is not None
    assert history["items"][0]["delivered_at"] is not None

    bob_view = client.get(CONVERSATIONS_URL, headers=bob_headers).json()
    assert bob_view["items"][0]["unread_count"] == 0


def test_read_frame_cannot_use_a_foreign_message(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    _, carol_headers = api.create_user("carol@example.com")

    bob_conversation = conversation_between(client, alice_headers, "bob")
    carol_conversation = conversation_between(client, alice_headers, "carol")

    own_message = client.post(
        f"{CONVERSATIONS_URL}/{bob_conversation}/messages",
        json={"body": "for bob"},
        headers=alice_headers,
    ).json()
    foreign_message = client.post(
        f"{CONVERSATIONS_URL}/{carol_conversation}/messages",
        json={"body": "for carol"},
        headers=carol_headers,
    ).json()

    with open_socket(client, api.login("bob@example.com")) as bob:
        bob.send_json(
            {
                "type": "read",
                "conversation_id": bob_conversation,
                "last_message_id": foreign_message["id"],
            }
        )
        error = bob.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "not_found"

        _expect_pong(bob, forbidden=("read",))

    history = client.get(
        f"{CONVERSATIONS_URL}/{bob_conversation}/messages", headers=bob_headers
    ).json()
    assert history["items"][0]["id"] == own_message["id"]
    assert history["items"][0]["read_at"] is None


def test_read_frame_is_throttled_per_connection(client, api, test_settings) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.create_user("bob@example.com")
    conversation = conversation_between(client, alice_headers)
    message = client.post(
        f"{CONVERSATIONS_URL}/{conversation}/messages",
        json={"body": "hello"},
        headers=alice_headers,
    ).json()
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with custom_client(test_settings, ws_read_min_interval_seconds=1.0) as fast_client:
        with (
            open_socket(fast_client, alice_token) as alice,
            open_socket(fast_client, bob_token) as bob,
        ):
            frame = {
                "type": "read",
                "conversation_id": conversation,
                "last_message_id": message["id"],
            }
            bob.send_json(frame)
            assert alice.receive_json()["type"] == "read"
            assert bob.receive_json()["type"] == "read"

            bob.send_json(frame)
            error = bob.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "rate_limited"
            assert error["retry_after_seconds"] >= 1

            _expect_pong(alice, forbidden=("read",))


# --- typing indicators ------------------------------------------------------


def test_typing_is_ephemeral_and_not_echoed(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers)
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with (
        open_socket(client, alice_token) as alice,
        open_socket(client, bob_token) as bob,
    ):
        alice.send_json(
            {"type": "typing", "conversation_id": conversation, "is_typing": True}
        )
        started = bob.receive_json()
        assert started["type"] == "typing"
        assert started["conversation_id"] == conversation
        assert started["username"] == "alice"
        assert started["is_typing"] is True
        assert started["expires_in_seconds"] >= 1

        # The sender's own devices do not get their own indicator.
        _expect_pong(alice, forbidden=("typing",))

        alice.send_json(
            {"type": "typing", "conversation_id": conversation, "is_typing": False}
        )
        stopped = bob.receive_json()
        assert stopped["type"] == "typing"
        assert stopped["is_typing"] is False

        # Typing frames are not messages and are never persisted.
        history = client.get(
            f"{CONVERSATIONS_URL}/{conversation}/messages", headers=alice_headers
        ).json()
        assert history["total"] == 0


def test_typing_is_suppressed_across_a_block(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = conversation_between(client, alice_headers)
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    assert (
        client.post(
            BLOCKS_URL, json={"username": "alice"}, headers=bob_headers
        ).status_code
        == 201
    )

    with (
        open_socket(client, alice_token) as alice,
        open_socket(client, bob_token) as bob,
    ):
        alice.send_json(
            {"type": "typing", "conversation_id": conversation, "is_typing": True}
        )
        error = alice.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "forbidden"

        _expect_pong(bob, forbidden=("typing",))


def test_typing_is_throttled_but_a_stop_is_not(client, api, test_settings) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers)
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with custom_client(
        test_settings, ws_typing_min_interval_seconds=1.0
    ) as fast_client:
        with (
            open_socket(fast_client, alice_token) as alice,
            open_socket(fast_client, bob_token) as bob,
        ):
            alice.send_json(
                {"type": "typing", "conversation_id": conversation, "is_typing": True}
            )
            assert bob.receive_json()["is_typing"] is True

            alice.send_json(
                {"type": "typing", "conversation_id": conversation, "is_typing": True}
            )
            error = alice.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "rate_limited"
            assert error["retry_after_seconds"] >= 1

            # A stop is always forwarded, even within the throttle window.
            alice.send_json(
                {"type": "typing", "conversation_id": conversation, "is_typing": False}
            )
            stopped = bob.receive_json()
            assert stopped["type"] == "typing"
            assert stopped["is_typing"] is False

            _expect_pong(bob, forbidden=("typing",))


def test_typing_requires_membership(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    api.create_user("carol@example.com")
    conversation = conversation_between(client, alice_headers)
    bob_token = api.login("bob@example.com")
    carol_token = api.login("carol@example.com")

    with (
        open_socket(client, carol_token) as carol,
        open_socket(client, bob_token) as bob,
    ):
        carol.send_json(
            {"type": "typing", "conversation_id": conversation, "is_typing": True}
        )
        error = carol.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "not_found"

        _expect_pong(bob, forbidden=("typing",))
