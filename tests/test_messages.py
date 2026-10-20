"""Message tests: sending, validation, history, paging, authorization and blocks."""

from __future__ import annotations

from app.core.security import MESSAGE_MAX_LENGTH
from tests.conftest import deactivate_user

CONVERSATIONS_URL = "/api/v1/conversations"


def _conversation(client, headers, username: str = "bob") -> int:
    response = client.post(
        CONVERSATIONS_URL, json={"username": username}, headers=headers
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _send(client, headers, conversation_id: int, body: str):
    return client.post(
        f"{CONVERSATIONS_URL}/{conversation_id}/messages",
        json={"body": body},
        headers=headers,
    )


def _history(client, headers, conversation_id: int, **params) -> dict:
    response = client.get(
        f"{CONVERSATIONS_URL}/{conversation_id}/messages", params=params, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- sending ----------------------------------------------------------------


def test_send_and_read_back_a_message(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation_id = _conversation(client, alice_headers)

    sent = _send(client, alice_headers, conversation_id, "Hello Bob")

    assert sent.status_code == 201
    message = sent.json()
    assert message["body"] == "Hello Bob"
    assert message["sender_username"] == "alice"
    assert message["conversation_id"] == conversation_id
    assert message["created_at"]

    # The recipient sees the same message.
    history = _history(client, bob_headers, conversation_id)
    assert history["total"] == 1
    assert history["items"][0]["id"] == message["id"]


def test_message_body_is_trimmed(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    message = _send(client, headers, conversation_id, "  padded  ").json()

    assert message["body"] == "padded"


def test_empty_and_blank_messages_are_rejected(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    assert _send(client, headers, conversation_id, "").status_code == 422
    assert _send(client, headers, conversation_id, "   \n\t ").status_code == 422
    assert _history(client, headers, conversation_id)["total"] == 0


def test_over_long_message_is_rejected(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    response = _send(client, headers, conversation_id, "x" * (MESSAGE_MAX_LENGTH + 1))

    assert response.status_code == 422
    assert "x" * (MESSAGE_MAX_LENGTH + 1) not in response.text


def test_message_at_the_limit_is_accepted(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    response = _send(client, headers, conversation_id, "x" * MESSAGE_MAX_LENGTH)

    assert response.status_code == 201
    assert len(response.json()["body"]) == MESSAGE_MAX_LENGTH


def test_sender_cannot_be_spoofed(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    response = client.post(
        f"{CONVERSATIONS_URL}/{conversation_id}/messages",
        json={"body": "hi", "sender_id": 999},
        headers=headers,
    )

    assert response.status_code == 422


def test_message_response_has_no_numeric_user_ids(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    message = _send(client, headers, conversation_id, "hi").json()

    assert set(message) == {
        "id",
        "conversation_id",
        "sender_username",
        "body",
        "created_at",
    }


def test_sending_updates_the_conversation_timestamp(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    _send(client, headers, conversation_id, "first")

    detail = client.get(f"{CONVERSATIONS_URL}/{conversation_id}", headers=headers).json()
    assert detail["message_count"] == 1
    assert detail["last_message_at"] is not None
    assert detail["last_message"]["body"] == "first"


# --- authorization ----------------------------------------------------------


def test_non_member_cannot_send(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    _, carol_headers = api.create_user("carol@example.com")
    conversation_id = _conversation(client, alice_headers)

    response = _send(client, carol_headers, conversation_id, "intruder")

    assert response.status_code == 404
    assert _history(client, alice_headers, conversation_id)["total"] == 0


def test_non_member_cannot_read_history(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    _, carol_headers = api.create_user("carol@example.com")
    conversation_id = _conversation(client, alice_headers)
    _send(client, alice_headers, conversation_id, "private")

    response = client.get(
        f"{CONVERSATIONS_URL}/{conversation_id}/messages", headers=carol_headers
    )

    assert response.status_code == 404
    assert "private" not in response.text


def test_unknown_conversation_is_not_found_for_messages(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    assert _send(client, headers, 999999, "hi").status_code == 404
    assert client.get(
        f"{CONVERSATIONS_URL}/999999/messages", headers=headers
    ).status_code == 404


def test_message_endpoints_require_authentication(client) -> None:
    assert client.post(
        f"{CONVERSATIONS_URL}/1/messages", json={"body": "hi"}
    ).status_code == 401
    assert client.get(f"{CONVERSATIONS_URL}/1/messages").status_code == 401


def test_disabled_account_cannot_send(client, api, db_session) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, alice_headers)
    deactivate_user(db_session, "alice@example.com")

    assert _send(client, alice_headers, conversation_id, "hi").status_code == 403


# --- history ----------------------------------------------------------------


def test_history_is_chronological_and_stable(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation_id = _conversation(client, alice_headers)

    for index in range(4):
        assert _send(client, alice_headers, conversation_id, f"m{index}").status_code == 201

    history = _history(client, bob_headers, conversation_id)

    assert [item["body"] for item in history["items"]] == ["m0", "m1", "m2", "m3"]
    ids = [item["id"] for item in history["items"]]
    assert ids == sorted(ids)
    assert all(
        item["sender_username"] == "alice" for item in history["items"]
    )


def test_history_is_paginated(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)
    for index in range(5):
        _send(client, headers, conversation_id, f"m{index}")

    pages = [
        _history(client, headers, conversation_id, limit=2, offset=offset)
        for offset in (0, 2, 4)
    ]

    assert all(page["total"] == 5 for page in pages)
    assert [len(page["items"]) for page in pages] == [2, 2, 1]
    bodies = [item["body"] for page in pages for item in page["items"]]
    assert bodies == ["m0", "m1", "m2", "m3", "m4"]


def test_history_limit_is_capped_by_the_server(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, headers)

    page = _history(client, headers, conversation_id, limit=200)

    assert page["limit"] == client.app.state.settings.message_max_limit


def test_histories_do_not_mix_between_conversations(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    api.register("carol@example.com")
    with_bob = _conversation(client, alice_headers, "bob")
    with_carol = _conversation(client, alice_headers, "carol")

    _send(client, alice_headers, with_bob, "for bob")
    _send(client, alice_headers, with_carol, "for carol")

    assert [i["body"] for i in _history(client, alice_headers, with_bob)["items"]] == [
        "for bob"
    ]
    assert [i["body"] for i in _history(client, alice_headers, with_carol)["items"]] == [
        "for carol"
    ]


# --- blocks -----------------------------------------------------------------


def test_blocking_stops_new_messages_but_keeps_history(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation_id = _conversation(client, alice_headers)
    _send(client, alice_headers, conversation_id, "before")

    client.post("/api/v1/blocks", json={"username": "bob"}, headers=alice_headers)

    # Both directions are refused.
    assert _send(client, alice_headers, conversation_id, "after").status_code == 403
    assert _send(client, bob_headers, conversation_id, "after").status_code == 403
    assert (
        _send(client, bob_headers, conversation_id, "after").json()["error"]["code"]
        == "forbidden"
    )

    # The existing history is still readable by both.
    assert _history(client, alice_headers, conversation_id)["total"] == 1
    assert _history(client, bob_headers, conversation_id)["items"][0]["body"] == "before"


def test_unblocking_restores_messaging(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation_id = _conversation(client, alice_headers)
    client.post("/api/v1/blocks", json={"username": "bob"}, headers=alice_headers)
    assert _send(client, alice_headers, conversation_id, "blocked").status_code == 403

    client.delete("/api/v1/blocks/bob", headers=alice_headers)

    assert _send(client, alice_headers, conversation_id, "unblocked").status_code == 201
