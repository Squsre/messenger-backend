"""Conversation tests: creation, idempotency, listing, authorization and blocks."""

from __future__ import annotations

from tests.conftest import deactivate_user

CONVERSATIONS_URL = "/api/v1/conversations"


def _create(client, headers, username: str):
    return client.post(CONVERSATIONS_URL, json={"username": username}, headers=headers)


def _partner(client, headers, username: str) -> dict:
    response = _create(client, headers, username)
    assert response.status_code in (200, 201), response.text
    return response.json()


# --- creation ---------------------------------------------------------------


def test_create_conversation_between_two_users(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")

    response = _create(client, alice_headers, "bob")

    assert response.status_code == 201
    body = response.json()
    assert body["participant"]["username"] == "bob"
    assert body["last_message"] is None
    assert body["last_message_at"] is None
    assert body["message_count"] == 0
    assert body["id"] > 0


def test_creating_the_same_conversation_twice_is_idempotent(client, api) -> None:
    """A duplicate pair must never produce a second conversation."""
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")

    first = _create(client, alice_headers, "bob")
    second = _create(client, alice_headers, "bob")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]

    listing = client.get(CONVERSATIONS_URL, headers=alice_headers).json()
    assert listing["total"] == 1


def test_either_participant_reopens_the_same_conversation(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    created = _create(client, alice_headers, "bob").json()
    reopened = _create(client, bob_headers, "alice")

    assert reopened.status_code == 200
    assert reopened.json()["id"] == created["id"]
    # From Bob's side the participant is Alice.
    assert reopened.json()["participant"]["username"] == "alice"


def test_cannot_start_a_conversation_with_yourself(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    response = _create(client, headers, "alice")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_cannot_start_a_conversation_with_an_unknown_user(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    response = _create(client, headers, "ghost")

    assert response.status_code == 404


def test_cannot_start_a_conversation_with_a_deactivated_user(
    client, api, db_session
) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    deactivate_user(db_session, "bob@example.com")

    assert _create(client, headers, "bob").status_code == 404


def test_conversation_pairs_are_independent(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    api.register("carol@example.com")

    with_bob = _partner(client, alice_headers, "bob")
    with_carol = _partner(client, alice_headers, "carol")

    assert with_bob["id"] != with_carol["id"]
    assert client.get(CONVERSATIONS_URL, headers=alice_headers).json()["total"] == 2


def test_creation_rejects_unknown_fields(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")

    response = client.post(
        CONVERSATIONS_URL,
        json={"username": "bob", "participant_id": 1},
        headers=headers,
    )

    assert response.status_code == 422


# --- listing ----------------------------------------------------------------


def test_list_conversations_returns_only_mine(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    _, carol_headers = api.create_user("carol@example.com")
    _create(client, alice_headers, "bob")

    alice_list = client.get(CONVERSATIONS_URL, headers=alice_headers).json()
    bob_list = client.get(CONVERSATIONS_URL, headers=bob_headers).json()
    carol_list = client.get(CONVERSATIONS_URL, headers=carol_headers).json()

    assert alice_list["total"] == 1
    assert bob_list["total"] == 1
    assert carol_list["total"] == 0
    # No email or numeric user id in the participant payload.
    participant = alice_list["items"][0]["participant"]
    assert participant["username"] == "bob"
    assert "email" not in participant
    assert "id" not in participant


def test_list_conversations_is_paginated(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    for index in range(5):
        api.register(f"member{index}@example.com")
        _create(client, headers, f"member{index}")

    first = client.get(
        CONVERSATIONS_URL, params={"limit": 2, "offset": 0}, headers=headers
    ).json()
    second = client.get(
        CONVERSATIONS_URL, params={"limit": 2, "offset": 2}, headers=headers
    ).json()

    assert first["total"] == 5
    assert len(first["items"]) == 2
    assert len(second["items"]) == 2
    ids = {item["id"] for item in first["items"]}
    assert ids & {item["id"] for item in second["items"]} == set()


def test_list_conversations_orders_by_recent_activity(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    api.register("carol@example.com")
    with_bob = _partner(client, headers, "bob")
    with_carol = _partner(client, headers, "carol")

    # Carol's conversation was created last, so it comes first.
    order = [item["id"] for item in client.get(CONVERSATIONS_URL, headers=headers).json()["items"]]
    assert order == [with_carol["id"], with_bob["id"]]

    # A message in the older conversation moves it to the top.
    client.post(
        f"{CONVERSATIONS_URL}/{with_bob['id']}/messages",
        json={"body": "bump"},
        headers=headers,
    )

    order = [item["id"] for item in client.get(CONVERSATIONS_URL, headers=headers).json()["items"]]
    assert order == [with_bob["id"], with_carol["id"]]


def test_list_includes_the_last_message_preview(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    conversation = _partner(client, headers, "bob")

    client.post(
        f"{CONVERSATIONS_URL}/{conversation['id']}/messages",
        json={"body": "see you then"},
        headers=headers,
    )

    (item,) = client.get(CONVERSATIONS_URL, headers=headers).json()["items"]
    assert item["last_message"]["body"] == "see you then"
    assert item["last_message"]["sender_username"] == "alice"
    assert item["last_message_at"] is not None


# --- detail and authorization ----------------------------------------------


def test_both_participants_can_read_the_conversation(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _partner(client, alice_headers, "bob")

    for headers in (alice_headers, bob_headers):
        response = client.get(f"{CONVERSATIONS_URL}/{conversation['id']}", headers=headers)
        assert response.status_code == 200
        assert response.json()["id"] == conversation["id"]


def test_outsiders_get_404_not_403(client, api) -> None:
    """A non-participant must not learn whether the conversation exists."""
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    _, carol_headers = api.create_user("carol@example.com")
    conversation = _partner(client, alice_headers, "bob")

    response = client.get(f"{CONVERSATIONS_URL}/{conversation['id']}", headers=carol_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_conversation_is_not_found(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    assert client.get(f"{CONVERSATIONS_URL}/999999", headers=headers).status_code == 404


def test_conversation_endpoints_require_authentication(client) -> None:
    assert client.get(CONVERSATIONS_URL).status_code == 401
    assert client.post(CONVERSATIONS_URL, json={"username": "bob"}).status_code == 401
    assert client.get(f"{CONVERSATIONS_URL}/1").status_code == 401


# --- blocks -----------------------------------------------------------------


def test_creating_a_conversation_across_a_block_is_forbidden(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    client.post("/api/v1/blocks", json={"username": "bob"}, headers=alice_headers)

    # Symmetric, even though the block row is directional.
    assert _create(client, alice_headers, "bob").status_code == 403
    assert _create(client, bob_headers, "alice").status_code == 403


def test_block_after_creation_keeps_the_conversation_listed(client, api) -> None:
    """Existing history is not deleted; only new messages are refused."""
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    conversation = _partner(client, alice_headers, "bob")

    client.post("/api/v1/blocks", json={"username": "bob"}, headers=alice_headers)

    assert client.get(CONVERSATIONS_URL, headers=alice_headers).json()["total"] == 1
    assert (
        client.get(f"{CONVERSATIONS_URL}/{conversation['id']}", headers=alice_headers).status_code
        == 200
    )


def test_participant_profile_respects_privacy(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    client.patch(
        "/api/v1/users/me/privacy",
        json={"show_last_seen": False},
        headers=bob_headers,
    )

    conversation = _partner(client, alice_headers, "bob")

    assert conversation["participant"]["last_seen_at"] is None
    assert "email" not in conversation["participant"]


def test_conversation_is_rate_limited(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    attempts = client.app.state.settings.chat_rate_limit_attempts

    for _ in range(attempts):
        assert client.get(CONVERSATIONS_URL, headers=headers).status_code == 200

    blocked = client.get(CONVERSATIONS_URL, headers=headers)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
