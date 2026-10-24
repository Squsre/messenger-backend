"""Realtime sending, validation and persistence tests."""

from __future__ import annotations

from app.core.security import MESSAGE_MAX_LENGTH
from tests._realtime import conversation_between, message_frame, open_socket


def test_message_is_stored_and_delivered_to_the_other_member(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, bob_token) as bob, open_socket(
        client, api.login("alice@example.com")
    ) as alice:
        alice.send_json(message_frame(conversation, "Hello Bob"))

        ack = alice.receive_json()
        assert ack["type"] == "ack"
        assert ack["duplicate"] is False
        assert ack["message"]["body"] == "Hello Bob"
        assert ack["message"]["sender_username"] == "alice"

        event = bob.receive_json()
        assert event["type"] == "message"
        assert event["message"]["id"] == ack["message"]["id"]

    # The message was committed before it was acknowledged.
    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["total"] == 1
    assert history["items"][0]["body"] == "Hello Bob"


def test_message_body_is_trimmed(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("alice@example.com")) as websocket:
        websocket.send_json(message_frame(conversation, "  padded  "))
        assert websocket.receive_json()["message"]["body"] == "padded"


def test_sender_cannot_be_spoofed(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("alice@example.com")) as websocket:
        websocket.send_json(
            {
                "type": "message",
                "conversation_id": conversation,
                "body": "hi",
                "sender_id": 999,
            }
        )
        event = websocket.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "invalid_payload"

        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["total"] == 0


def test_empty_and_oversized_bodies_are_rejected(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("alice@example.com")) as websocket:
        for body in ("   ", "x" * (MESSAGE_MAX_LENGTH + 1)):
            websocket.send_json(message_frame(conversation, body))
            event = websocket.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "invalid_payload"

        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"


def test_a_body_at_the_limit_is_accepted(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("alice@example.com")) as websocket:
        websocket.send_json(message_frame(conversation, "x" * MESSAGE_MAX_LENGTH))
        ack = websocket.receive_json()
        assert ack["type"] == "ack"
        assert len(ack["message"]["body"]) == MESSAGE_MAX_LENGTH


def test_invalid_json_and_unknown_types_are_rejected(client, api) -> None:
    api.register("alice@example.com")
    with open_socket(client, api.login("alice@example.com")) as websocket:
        websocket.send_text("this is not json")
        assert websocket.receive_json()["code"] == "invalid_payload"

        websocket.send_json({"type": "subscribe", "conversation_id": 1})
        assert websocket.receive_json()["code"] == "unknown_type"

        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"


def test_binary_frames_are_rejected(client, api) -> None:
    api.register("alice@example.com")
    with open_socket(client, api.login("alice@example.com")) as websocket:
        websocket.send_bytes(b'{"type": "ping"}')
        event = websocket.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "unsupported_frame"

        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"


def test_duplicate_client_message_id_is_not_stored_twice(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")
    frame = message_frame(conversation, "once", client_message_id="retry-key-1")

    with open_socket(client, bob_token) as bob, open_socket(
        client, api.login("alice@example.com")
    ) as alice:
        alice.send_json(frame)
        first = alice.receive_json()
        assert first["type"] == "ack"
        assert first["duplicate"] is False

        alice.send_json(frame)
        second = alice.receive_json()
        assert second["type"] == "ack"
        assert second["duplicate"] is True
        assert second["client_message_id"] == "retry-key-1"
        assert second["message"]["id"] == first["message"]["id"]

        # Only the first send was broadcast; the next frame Bob sees is a pong.
        assert bob.receive_json()["message"]["id"] == first["message"]["id"]
        bob.send_json({"type": "ping"})
        assert bob.receive_json()["type"] == "pong"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["total"] == 1


def test_offline_recipient_keeps_the_message(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    # Bob never connects: the send must still succeed and persist.
    with open_socket(client, api.login("alice@example.com")) as alice:
        alice.send_json(message_frame(conversation, "stored for later"))
        ack = alice.receive_json()
        assert ack["type"] == "ack"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=bob_headers
    ).json()
    assert history["total"] == 1
    assert history["items"][0]["body"] == "stored for later"


def test_ping_pong_keeps_the_socket_alive(client, api) -> None:
    api.register("alice@example.com")
    with open_socket(client, api.login("alice@example.com")) as websocket:
        websocket.send_json({"type": "ping"})
        pong = websocket.receive_json()
        assert pong["type"] == "pong"
        assert pong["server_time"]


def test_ack_shape_has_no_numeric_user_ids(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("alice@example.com")) as websocket:
        websocket.send_json(message_frame(conversation, "hi"))
        message = websocket.receive_json()["message"]

    assert set(message) == {
        "id",
        "conversation_id",
        "sender_username",
        "body",
        "created_at",
        "delivered_at",
        "read_at",
    }
