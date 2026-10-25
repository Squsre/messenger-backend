"""WebSocket tests for group events, message fan-out and authorization."""

from __future__ import annotations

from tests._groups import add_member, create_group, send_group_message
from tests._realtime import (
    conversation_between,
    message_frame,
    open_socket,
)


def _tokens(api):
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")
    carol_token = api.login("carol@example.com")
    return alice_token, bob_token, carol_token


def test_group_created_event_is_sent_only_to_the_owner(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    alice_token = api.login("alice@example.com")

    with open_socket(client, alice_token) as alice_socket:
        group = create_group(client, alice, name="Socket Group")
        event = alice_socket.receive_json()
        assert event["type"] == "group_created", event
        assert event["group"]["id"] == group["id"]
        assert event["group"]["owner_username"] == "alice"


def test_group_update_event_is_built_per_member(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice, name="Before Rename")
    add_member(client, alice, bob, group["id"], username="bob")
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with open_socket(client, alice_token) as alice_socket, open_socket(
        client, bob_token
    ) as bob_socket:
        response = client.patch(
            f"/api/v1/groups/{group['id']}",
            json={"name": "After Rename"},
            headers=alice,
        )
        assert response.status_code == 200, response.text

        alice_event = alice_socket.receive_json()
        bob_event = bob_socket.receive_json()
        assert alice_event["type"] == "group_updated", alice_event
        assert bob_event["type"] == "group_updated", bob_event
        assert alice_event["group"]["name"] == "After Rename"
        assert bob_event["group"]["name"] == "After Rename"
        assert alice_event["group"]["role"] == "owner"
        assert bob_event["group"]["role"] == "member"
        assert alice_event["group"]["unread_count"] == 0
        assert bob_event["group"]["unread_count"] == 0


def test_group_message_is_delivered_only_to_members(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    _, carol = api.create_user("carol@example.com", username="carol")
    group = create_group(client, alice, name="Socket Chat")
    add_member(client, alice, bob, group["id"], username="bob")
    direct_id = conversation_between(client, alice, "carol")
    alice_token, bob_token, carol_token = _tokens(api)

    with open_socket(client, alice_token) as alice_socket, open_socket(
        client, bob_token
    ) as bob_socket, open_socket(client, carol_token) as carol_socket:
        alice_socket.send_json(message_frame(group["id"], "hello members"))
        ack = alice_socket.receive_json()
        assert ack["type"] == "ack", ack
        assert ack["message"]["conversation_id"] == group["id"]
        assert ack["message"]["body"] == "hello members"

        bob_event = bob_socket.receive_json()
        assert bob_event["type"] == "message", bob_event
        assert bob_event["message"]["conversation_id"] == group["id"]
        assert bob_event["message"]["body"] == "hello members"

        # Carol is not a member. Send a direct message afterwards; if the group
        # message leaked, it would be Carol's first event.
        alice_socket.send_json(message_frame(direct_id, "direct sentinel"))
        direct_ack = alice_socket.receive_json()
        assert direct_ack["type"] == "ack"
        carol_event = carol_socket.receive_json()
        assert carol_event["type"] == "message", carol_event
        assert carol_event["message"]["conversation_id"] == direct_id
        assert carol_event["message"]["body"] == "direct sentinel"


def test_group_websocket_retry_is_idempotent_and_does_not_duplicate_receipts(
    client, api
) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")
    direct_id = conversation_between(client, alice, "bob")
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with open_socket(client, alice_token) as alice_socket, open_socket(
        client, bob_token
    ) as bob_socket:
        frame = message_frame(group["id"], "retry me", "group-retry-1")
        alice_socket.send_json(frame)
        first_ack = alice_socket.receive_json()
        assert first_ack["type"] == "ack", first_ack
        assert first_ack["duplicate"] is False

        first_event = bob_socket.receive_json()
        assert first_event["type"] == "message", first_event
        assert first_event["message"]["conversation_id"] == group["id"]

        alice_socket.send_json(frame)
        retry_ack = alice_socket.receive_json()
        assert retry_ack["type"] == "ack", retry_ack
        assert retry_ack["duplicate"] is True
        assert retry_ack["message"]["id"] == first_ack["message"]["id"]

        # A retry must not produce a second group event. Send a direct
        # sentinel afterwards; Bob's next event must be that direct message.
        alice_socket.send_json(message_frame(direct_id, "after retry"))
        direct_ack = alice_socket.receive_json()
        assert direct_ack["type"] == "ack"
        next_bob_event = bob_socket.receive_json()
        assert next_bob_event["type"] == "message", next_bob_event
        assert next_bob_event["message"]["conversation_id"] == direct_id

    history = client.get(
        f"/api/v1/groups/{group['id']}/messages", headers=alice
    ).json()
    assert history["total"] == 1
    assert history["items"][0]["delivered_count"] == 1


def test_non_member_websocket_send_is_rejected_and_socket_stays_usable(
    client, api
) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice)
    direct_id = conversation_between(client, alice, "bob")
    bob_token = api.login("bob@example.com")

    with open_socket(client, bob_token) as bob_socket:
        bob_socket.send_json(message_frame(group["id"], "not allowed"))
        error = bob_socket.receive_json()
        assert error["type"] == "error", error
        assert error["code"] == "not_found", error

        # The socket remains authenticated and usable for allowed conversations.
        bob_socket.send_json(message_frame(direct_id, "allowed"))
        ack = bob_socket.receive_json()
        assert ack["type"] == "ack", ack
        assert ack["message"]["conversation_id"] == direct_id


def test_group_read_event_is_fanned_out_to_members(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    _, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")
    sent = send_group_message(client, alice, group["id"], "read me")
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with open_socket(client, alice_token) as alice_socket, open_socket(
        client, bob_token
    ) as bob_socket:
        bob_socket.send_json(
            {
                "type": "read",
                "conversation_id": group["id"],
                "last_message_id": sent["id"],
            }
        )
        bob_event = bob_socket.receive_json()
        assert bob_event["type"] == "read", bob_event
        assert bob_event["conversation_id"] == group["id"]
        assert bob_event["reader_username"] == "bob"

        alice_event = alice_socket.receive_json()
        assert alice_event["type"] == "read", alice_event
        assert alice_event["conversation_id"] == group["id"]
        assert alice_event["reader_username"] == "bob"

    # Bob's read did not mark the message read for Alice (she is the sender) or
    # for a third member; the per-member receipt table is the source of truth.
    history = client.get(
        f"/api/v1/groups/{group['id']}/messages", headers=alice
    ).json()
    assert history["items"][0]["read_count"] == 1


def test_removed_member_stops_receiving_group_events(client, api) -> None:
    _, alice = api.create_user("alice@example.com", username="alice")
    bob_account, bob = api.create_user("bob@example.com", username="bob")
    group = create_group(client, alice)
    add_member(client, alice, bob, group["id"], username="bob")
    direct_id = conversation_between(client, alice, "bob")
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with open_socket(client, alice_token) as alice_socket, open_socket(
        client, bob_token
    ) as bob_socket:
        removed = client.delete(
            f"/api/v1/groups/{group['id']}/members/{bob_account['id']}",
            headers=alice,
        )
        assert removed.status_code == 204
        # The removal event goes to the remaining members, not to Bob.
        alice_event = alice_socket.receive_json()
        assert alice_event["type"] == "group_member_removed", alice_event

        send_group_message(client, alice, group["id"], "after removal")
        group_event = alice_socket.receive_json()
        assert group_event["type"] == "message", group_event
        assert group_event["message"]["conversation_id"] == group["id"]
        alice_socket.send_json(message_frame(direct_id, "direct after removal"))
        direct_ack = alice_socket.receive_json()
        assert direct_ack["type"] == "ack"
        bob_event = bob_socket.receive_json()
        assert bob_event["type"] == "message", bob_event
        assert bob_event["message"]["conversation_id"] == direct_id
