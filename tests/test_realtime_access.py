"""Membership, block and audience-isolation tests for the realtime socket."""

from __future__ import annotations

from tests._realtime import conversation_between, message_frame, open_socket

BLOCKS_URL = "/api/v1/blocks"


def test_non_member_cannot_send(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    api.register("carol@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("carol@example.com")) as carol:
        carol.send_json(message_frame(conversation, "not mine"))
        event = carol.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "not_found"

        carol.send_json({"type": "ping"})
        assert carol.receive_json()["type"] == "pong"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=bob_headers
    ).json()
    assert history["total"] == 0


def test_unknown_conversation_is_not_found(client, api) -> None:
    api.register("alice@example.com")
    with open_socket(client, api.login("alice@example.com")) as alice:
        alice.send_json(message_frame(999_999, "ghost"))
        event = alice.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "not_found"


def test_non_member_does_not_receive_messages(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    api.register("carol@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, bob_token) as bob, open_socket(
        client, api.login("carol@example.com")
    ) as carol, open_socket(client, api.login("alice@example.com")) as alice:
        alice.send_json(message_frame(conversation, "for bob only"))
        assert alice.receive_json()["type"] == "ack"
        assert bob.receive_json()["message"]["body"] == "for bob only"

        # Frames are FIFO: a pong arriving first proves nothing was queued for
        # Carol before it.
        carol.send_json({"type": "ping"})
        assert carol.receive_json()["type"] == "pong"


def test_blocked_pair_cannot_send_over_the_socket(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    blocked = client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)
    assert blocked.status_code == 201

    with open_socket(client, bob_token) as bob, open_socket(
        client, api.login("alice@example.com")
    ) as alice:
        alice.send_json(message_frame(conversation, "should not pass"))
        event = alice.receive_json()
        assert event["type"] == "error"
        assert event["code"] == "forbidden"
        assert event["message"] == "This user is not available."

        # The block is reversible, so the socket stays open.
        alice.send_json({"type": "ping"})
        assert alice.receive_json()["type"] == "pong"
        bob.send_json({"type": "ping"})
        assert bob.receive_json()["type"] == "pong"

    history = client.get(
        f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["total"] == 0


def test_block_keeps_history_and_unblocking_restores_sending(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")

    with open_socket(client, api.login("alice@example.com")) as alice:
        alice.send_json(message_frame(conversation, "before the block"))
        assert alice.receive_json()["type"] == "ack"

        blocked = client.post(
            BLOCKS_URL, json={"username": "bob"}, headers=alice_headers
        )
        assert blocked.status_code == 201

        alice.send_json(message_frame(conversation, "while blocked"))
        assert alice.receive_json()["code"] == "forbidden"

        # History is still readable while blocked.
        history = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=alice_headers
        )
        assert history.status_code == 200
        assert [item["body"] for item in history.json()["items"]] == [
            "before the block"
        ]

        unblocked = client.delete(f"{BLOCKS_URL}/bob", headers=alice_headers)
        assert unblocked.status_code == 204

        alice.send_json(message_frame(conversation, "after the unblock"))
        ack = alice.receive_json()
        assert ack["type"] == "ack"
        assert ack["message"]["body"] == "after the unblock"


def test_conversations_are_isolated(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    api.register("carol@example.com")
    carol_token = api.login("carol@example.com")
    bob_conversation = conversation_between(client, alice_headers, "bob")
    carol_conversation = conversation_between(client, alice_headers, "carol")

    with open_socket(client, bob_token) as bob, open_socket(
        client, carol_token
    ) as carol, open_socket(client, api.login("alice@example.com")) as alice:
        alice.send_json(message_frame(bob_conversation, "hi bob"))
        assert alice.receive_json()["type"] == "ack"
        assert bob.receive_json()["message"]["body"] == "hi bob"

        carol.send_json({"type": "ping"})
        assert carol.receive_json()["type"] == "pong"

        alice.send_json(message_frame(carol_conversation, "hi carol"))
        assert alice.receive_json()["type"] == "ack"
        assert carol.receive_json()["message"]["body"] == "hi carol"

        bob.send_json({"type": "ping"})
        assert bob.receive_json()["type"] == "pong"


def test_sender_devices_receive_but_the_origin_gets_only_an_ack(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    bob_token = api.login("bob@example.com")
    conversation = conversation_between(client, alice_headers, "bob")
    alice_token = api.login("alice@example.com")

    with open_socket(client, alice_token) as laptop, open_socket(
        client, alice_token
    ) as phone, open_socket(client, bob_token) as bob:
        laptop.send_json(message_frame(conversation, "from the laptop"))

        ack = laptop.receive_json()
        assert ack["type"] == "ack"
        message_id = ack["message"]["id"]

        phone_event = phone.receive_json()
        assert phone_event["type"] == "message"
        assert phone_event["message"]["id"] == message_id
        assert bob.receive_json()["message"]["id"] == message_id

        # The originating socket is excluded from the fan-out (it got the ack).
        laptop.send_json({"type": "ping"})
        assert laptop.receive_json()["type"] == "pong"
