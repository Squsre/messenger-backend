"""HTTP tests for delivery/read status, unread counts and last-seen privacy.

The WebSocket behaviour that produces these states is exercised separately in
``tests/test_realtime_status.py``; here the focus is the persisted state, the new
read endpoint, authorization and the conversation list.
"""

from __future__ import annotations

import time

from sqlalchemy import select

from app.db.models import User
from tests._realtime import open_socket

CONVERSATIONS_URL = "/api/v1/conversations"


def _send(client, headers: dict, conversation_id: int, body: str):
    return client.post(
        f"{CONVERSATIONS_URL}/{conversation_id}/messages",
        json={"body": body},
        headers=headers,
    )


def _read(client, headers: dict, conversation_id: int, last_message_id: int):
    return client.post(
        f"{CONVERSATIONS_URL}/{conversation_id}/read",
        json={"last_message_id": last_message_id},
        headers=headers,
    )


def _conversation(client, headers: dict, username: str = "bob") -> int:
    response = client.post(
        CONVERSATIONS_URL, json={"username": username}, headers=headers
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _last_seen(db_session, email: str):
    # Start a fresh read transaction so the value written by the server thread
    # (the WebSocket cleanup) is visible.
    db_session.rollback()
    return db_session.execute(
        select(User.last_seen_at).where(User.email == email)
    ).scalar_one()


def _wait_for(predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before the timeout")


# --- delivery ---------------------------------------------------------------


def test_offline_message_starts_undelivered_and_unread(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _conversation(client, alice_headers)

    response = _send(client, alice_headers, conversation, "hello")
    assert response.status_code == 201, response.text
    message = response.json()
    assert message["delivered_at"] is None
    assert message["read_at"] is None

    alice_view = client.get(CONVERSATIONS_URL, headers=alice_headers).json()
    assert alice_view["items"][0]["unread_count"] == 0
    assert alice_view["items"][0]["last_message"]["read_at"] is None

    bob_view = client.get(CONVERSATIONS_URL, headers=bob_headers).json()
    assert bob_view["items"][0]["unread_count"] == 1


def test_online_recipient_gets_a_delivered_message(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _conversation(client, alice_headers)
    bob_token = api.login("bob@example.com")

    with open_socket(client, bob_token) as bob:
        response = _send(client, alice_headers, conversation, "while you were here")
        assert response.status_code == 201
        sent = response.json()
        assert sent["delivered_at"] is not None
        assert sent["read_at"] is None

        event = bob.receive_json()
        assert event["type"] == "message"
        assert event["message"]["id"] == sent["id"]
        assert event["message"]["delivered_at"] is not None

    bob_view = client.get(CONVERSATIONS_URL, headers=bob_headers).json()
    assert bob_view["items"][0]["unread_count"] == 1


def test_http_send_reaches_the_senders_other_devices_with_status(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = _conversation(client, alice_headers)
    alice_token = api.login("alice@example.com")
    bob_token = api.login("bob@example.com")

    with open_socket(client, alice_token) as alice, open_socket(client, bob_token) as bob:
        sent = _send(client, alice_headers, conversation, "from http").json()
        assert sent["delivered_at"] is not None

        event = alice.receive_json()
        assert event["type"] == "message"
        assert event["message"]["id"] == sent["id"]

        delivered = alice.receive_json()
        assert delivered["type"] == "delivered"
        assert delivered["message_id"] == sent["id"]
        assert delivered["delivered_at"] is not None

        assert bob.receive_json()["message"]["id"] == sent["id"]


# --- reading ----------------------------------------------------------------


def test_mark_read_sets_read_and_delivered_and_clears_unread(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _conversation(client, alice_headers)

    first = _send(client, alice_headers, conversation, "one").json()
    second = _send(client, alice_headers, conversation, "two").json()

    before = client.get(CONVERSATIONS_URL, headers=bob_headers).json()
    assert before["items"][0]["unread_count"] == 2
    detail_before = client.get(
        f"{CONVERSATIONS_URL}/{conversation}", headers=bob_headers
    ).json()
    assert detail_before["unread_count"] == 2

    receipt = _read(client, bob_headers, conversation, second["id"])
    assert receipt.status_code == 200, receipt.text
    body = receipt.json()
    assert body["updated"] == 2
    assert body["read_at"] is not None
    assert body["unread_count"] == 0
    assert body["last_read_message_id"] == second["id"]

    detail_after = client.get(
        f"{CONVERSATIONS_URL}/{conversation}", headers=bob_headers
    ).json()
    assert detail_after["unread_count"] == 0

    history = client.get(
        f"{CONVERSATIONS_URL}/{conversation}/messages", headers=alice_headers
    ).json()
    assert [item["id"] for item in history["items"]] == [first["id"], second["id"]]
    assert all(item["delivered_at"] is not None for item in history["items"])
    assert all(item["read_at"] is not None for item in history["items"])

    after = client.get(CONVERSATIONS_URL, headers=bob_headers).json()
    assert after["items"][0]["unread_count"] == 0


def test_mark_read_only_affects_messages_up_to_the_watermark(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _conversation(client, alice_headers)

    first = _send(client, alice_headers, conversation, "one").json()
    second = _send(client, alice_headers, conversation, "two").json()
    third = _send(client, alice_headers, conversation, "three").json()

    receipt = _read(client, bob_headers, conversation, second["id"]).json()
    assert receipt["updated"] == 2
    assert receipt["unread_count"] == 1

    history = client.get(
        f"{CONVERSATIONS_URL}/{conversation}/messages", headers=bob_headers
    ).json()
    by_id = {item["id"]: item for item in history["items"]}
    assert by_id[first["id"]]["read_at"] is not None
    assert by_id[second["id"]]["read_at"] is not None
    assert by_id[third["id"]]["read_at"] is None

    final = _read(client, bob_headers, conversation, third["id"]).json()
    assert final["updated"] == 1
    assert final["unread_count"] == 0


def test_marking_read_twice_is_idempotent(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _conversation(client, alice_headers)
    message = _send(client, alice_headers, conversation, "once").json()

    first = _read(client, bob_headers, conversation, message["id"])
    assert first.status_code == 200
    assert first.json()["updated"] == 1

    second = _read(client, bob_headers, conversation, message["id"])
    assert second.status_code == 200
    assert second.json()["updated"] == 0
    assert second.json()["read_at"] is None
    assert second.json()["unread_count"] == 0


# --- authorization ----------------------------------------------------------


def test_read_requires_membership(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    _, carol_headers = api.create_user("carol@example.com")
    conversation = _conversation(client, alice_headers)
    message = _send(client, alice_headers, conversation, "private").json()

    response = _read(client, carol_headers, conversation, message["id"])
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

    history = client.get(
        f"{CONVERSATIONS_URL}/{conversation}/messages", headers=alice_headers
    ).json()
    assert history["items"][0]["read_at"] is None


def test_read_rejects_a_message_from_another_conversation(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    _, carol_headers = api.create_user("carol@example.com")

    bob_conversation = _conversation(client, alice_headers, "bob")
    carol_conversation = _conversation(client, alice_headers, "carol")

    bob_message = _send(client, alice_headers, bob_conversation, "for bob").json()
    carol_message = _send(client, alice_headers, carol_conversation, "for carol").json()

    response = _read(client, bob_headers, bob_conversation, carol_message["id"])
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

    history = client.get(
        f"{CONVERSATIONS_URL}/{bob_conversation}/messages", headers=bob_headers
    ).json()
    assert history["items"][0]["id"] == bob_message["id"]
    assert history["items"][0]["read_at"] is None


def test_read_rejects_an_unknown_message_id(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _conversation(client, alice_headers)
    _send(client, alice_headers, conversation, "hello")

    response = _read(client, bob_headers, conversation, 999_999)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- last seen --------------------------------------------------------------


def test_websocket_disconnect_records_last_seen(client, api, db_session) -> None:
    api.register("bob@example.com")
    token = api.login("bob@example.com")
    before = _last_seen(db_session, "bob@example.com")

    with open_socket(client, token):
        pass

    if before is None:
        _wait_for(lambda: _last_seen(db_session, "bob@example.com") is not None)
    else:
        _wait_for(lambda: _last_seen(db_session, "bob@example.com") > before)


def test_last_seen_is_hidden_when_the_owner_disables_it(
    client, api, db_session
) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _conversation(client, alice_headers)
    api.set_profile(bob_headers, show_last_seen=False)
    bob_token = api.login("bob@example.com")

    with open_socket(client, bob_token):
        pass

    _wait_for(lambda: _last_seen(db_session, "bob@example.com") is not None)

    detail = client.get(
        f"{CONVERSATIONS_URL}/{conversation}", headers=alice_headers
    ).json()
    assert detail["participant"]["last_seen_at"] is None

    # The owner still sees their own timestamp.
    me = client.get("/api/v1/users/me", headers=bob_headers).json()
    assert me["last_seen_at"] is not None
