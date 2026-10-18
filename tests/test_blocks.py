"""Block tests, including how a block interacts with profiles, search and contacts."""

from __future__ import annotations

from tests.conftest import deactivate_user

BLOCKS_URL = "/api/v1/blocks"
CONTACTS_URL = "/api/v1/contacts"
SEARCH_URL = "/api/v1/users/search"


def _profile_url(username: str) -> str:
    return f"/api/v1/users/{username}"


def test_block_list_and_unblock(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")

    created = client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)

    assert created.status_code == 201
    assert created.json()["username"] == "bob"
    assert "email" not in created.json()

    listed = client.get(BLOCKS_URL, headers=alice_headers).json()
    assert listed["total"] == 1
    assert [item["username"] for item in listed["items"]] == ["bob"]

    removed = client.delete(f"{BLOCKS_URL}/bob", headers=alice_headers)
    assert removed.status_code == 204
    assert client.get(BLOCKS_URL, headers=alice_headers).json()["total"] == 0


def test_blocking_the_same_user_twice_is_rejected(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    client.post(BLOCKS_URL, json={"username": "bob"}, headers=headers)

    response = client.post(BLOCKS_URL, json={"username": "bob"}, headers=headers)

    assert response.status_code == 409


def test_cannot_block_yourself(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    response = client.post(BLOCKS_URL, json={"username": "alice"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_cannot_block_an_unknown_or_deactivated_user(client, api, db_session) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    deactivate_user(db_session, "bob@example.com")

    assert client.post(BLOCKS_URL, json={"username": "ghost"}, headers=headers).status_code == 404
    assert client.post(BLOCKS_URL, json={"username": "bob"}, headers=headers).status_code == 404


def test_unblocking_a_missing_block_is_not_found(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")

    assert client.delete(f"{BLOCKS_URL}/bob", headers=headers).status_code == 404
    assert client.delete(f"{BLOCKS_URL}/ghost", headers=headers).status_code == 404


def test_a_block_hides_each_profile_from_the_other(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    assert client.get(_profile_url("bob"), headers=alice_headers).status_code == 200

    client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)

    # Symmetric effect, even though the row is directional.
    assert client.get(_profile_url("bob"), headers=alice_headers).status_code == 404
    assert client.get(_profile_url("alice"), headers=bob_headers).status_code == 404
    # The blocker does not lose access to their own profile or block list.
    assert client.get("/api/v1/users/me", headers=alice_headers).status_code == 200
    assert client.get(BLOCKS_URL, headers=alice_headers).json()["total"] == 1


def test_unblocking_restores_access(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)

    client.delete(f"{BLOCKS_URL}/bob", headers=alice_headers)

    assert client.get(_profile_url("bob"), headers=alice_headers).status_code == 200


def test_a_block_prevents_contact_operations(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)

    # Neither side can add the other.
    assert (
        client.post(CONTACTS_URL, json={"username": "bob"}, headers=alice_headers).status_code == 403
    )
    assert (
        client.post(CONTACTS_URL, json={"username": "alice"}, headers=bob_headers).status_code == 403
    )


def test_blocking_hides_an_existing_contact_without_deleting_it(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    client.post(CONTACTS_URL, json={"username": "bob"}, headers=alice_headers)

    client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)
    assert client.get(CONTACTS_URL, headers=alice_headers).json()["total"] == 0

    # The relationship survives, so unblocking restores the list.
    client.delete(f"{BLOCKS_URL}/bob", headers=alice_headers)
    listed = client.get(CONTACTS_URL, headers=alice_headers).json()
    assert listed["total"] == 1
    assert listed["items"][0]["username"] == "bob"
    assert client.get(CONTACTS_URL, headers=bob_headers).json()["total"] == 0


def test_a_block_removes_both_users_from_each_others_search(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)

    assert client.get(SEARCH_URL, params={"q": "bob"}, headers=alice_headers).json()["items"] == []
    assert client.get(SEARCH_URL, params={"q": "alice"}, headers=bob_headers).json()["items"] == []


def test_block_endpoints_require_authentication(client) -> None:
    assert client.get(BLOCKS_URL).status_code == 401
    assert client.post(BLOCKS_URL, json={"username": "bob"}).status_code == 401
    assert client.delete(f"{BLOCKS_URL}/bob").status_code == 401


def test_a_disabled_account_cannot_manage_blocks(client, api, db_session) -> None:
    _, headers = api.create_user("alice@example.com")
    deactivate_user(db_session, "alice@example.com")

    assert client.get(BLOCKS_URL, headers=headers).status_code == 403


def test_block_listing_is_paginated(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    for index in range(5):
        api.register(f"member{index}@example.com")
        client.post(BLOCKS_URL, json={"username": f"member{index}"}, headers=headers)

    page = client.get(BLOCKS_URL, params={"limit": 2, "offset": 0}, headers=headers).json()

    assert page["total"] == 5
    assert len(page["items"]) == 2


def test_a_blocked_user_stays_blocked_after_being_deactivated(client, api, db_session) -> None:
    """The block list must remain usable even if the other account is disabled."""
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    client.post(BLOCKS_URL, json={"username": "bob"}, headers=alice_headers)

    deactivate_user(db_session, "bob@example.com")

    # Still listed, and still removable.
    assert client.get(BLOCKS_URL, headers=alice_headers).json()["total"] == 1
    assert client.delete(f"{BLOCKS_URL}/bob", headers=alice_headers).status_code == 204
