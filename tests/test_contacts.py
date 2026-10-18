"""Contact-list tests: one-directional relationships, access control, paging."""

from __future__ import annotations

from tests.conftest import deactivate_user

CONTACTS_URL = "/api/v1/contacts"


def test_add_list_and_remove_a_contact(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.register("bob@example.com")

    created = client.post(CONTACTS_URL, json={"username": "bob"}, headers=alice_headers)

    assert created.status_code == 201
    assert created.json()["username"] == "bob"
    assert "email" not in created.json()

    listed = client.get(CONTACTS_URL, headers=alice_headers).json()
    assert listed["total"] == 1
    assert [item["username"] for item in listed["items"]] == ["bob"]

    removed = client.delete(f"{CONTACTS_URL}/bob", headers=alice_headers)
    assert removed.status_code == 204
    assert removed.content == b""
    assert client.get(CONTACTS_URL, headers=alice_headers).json()["total"] == 0


def test_adding_the_same_contact_twice_is_rejected(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    client.post(CONTACTS_URL, json={"username": "bob"}, headers=headers)

    response = client.post(CONTACTS_URL, json={"username": "bob"}, headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_cannot_add_yourself(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    response = client.post(CONTACTS_URL, json={"username": "alice"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_cannot_add_an_unknown_user(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    response = client.post(CONTACTS_URL, json={"username": "ghost"}, headers=headers)

    assert response.status_code == 404


def test_cannot_add_a_deactivated_user(client, api, db_session) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    deactivate_user(db_session, "bob@example.com")

    response = client.post(CONTACTS_URL, json={"username": "bob"}, headers=headers)

    assert response.status_code == 404


def test_contacts_are_one_directional(client, api) -> None:
    """Adding bob to alice's contacts does not add alice to bob's."""
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    client.post(CONTACTS_URL, json={"username": "bob"}, headers=alice_headers)

    assert client.get(CONTACTS_URL, headers=alice_headers).json()["total"] == 1
    assert client.get(CONTACTS_URL, headers=bob_headers).json()["total"] == 0


def test_a_contact_list_is_private_to_its_owner(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    api.register("carol@example.com")
    client.post(CONTACTS_URL, json={"username": "carol"}, headers=alice_headers)

    assert client.get(CONTACTS_URL, headers=bob_headers).json()["total"] == 0


def test_removing_a_missing_contact_is_not_found(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")

    assert client.delete(f"{CONTACTS_URL}/bob", headers=headers).status_code == 404


def test_removing_an_unknown_user_is_not_found(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    assert client.delete(f"{CONTACTS_URL}/ghost", headers=headers).status_code == 404


def test_cannot_remove_another_users_contact_relationship(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    api.register("carol@example.com")
    client.post(CONTACTS_URL, json={"username": "carol"}, headers=alice_headers)

    # Bob does not own that relationship, so it does not exist for him.
    assert client.delete(f"{CONTACTS_URL}/carol", headers=bob_headers).status_code == 404
    assert client.get(CONTACTS_URL, headers=alice_headers).json()["total"] == 1


def test_contact_list_is_paginated(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    for index in range(5):
        api.register(f"member{index}@example.com")
        client.post(CONTACTS_URL, json={"username": f"member{index}"}, headers=headers)

    first = client.get(CONTACTS_URL, params={"limit": 2, "offset": 0}, headers=headers).json()
    second = client.get(CONTACTS_URL, params={"limit": 2, "offset": 2}, headers=headers).json()

    assert first["total"] == 5
    assert len(first["items"]) == 2
    assert len(second["items"]) == 2
    assert {item["username"] for item in first["items"]} & {
        item["username"] for item in second["items"]
    } == set()


def test_contact_operations_require_authentication(client) -> None:
    assert client.get(CONTACTS_URL).status_code == 401
    assert client.post(CONTACTS_URL, json={"username": "bob"}).status_code == 401
    assert client.delete(f"{CONTACTS_URL}/bob").status_code == 401


def test_disabled_account_cannot_manage_contacts(client, api, db_session) -> None:
    _, headers = api.create_user("alice@example.com")
    deactivate_user(db_session, "alice@example.com")

    assert client.get(CONTACTS_URL, headers=headers).status_code == 403


def test_unknown_fields_are_rejected(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    response = client.post(
        CONTACTS_URL,
        json={"username": "bob", "owner_id": 1},
        headers=headers,
    )

    assert response.status_code == 422


def test_contact_list_shows_public_fields_only(client, api) -> None:
    _, headers = api.create_user("alice@example.com")
    api.register("bob@example.com")
    client.post(CONTACTS_URL, json={"username": "bob"}, headers=headers)

    (item,) = client.get(CONTACTS_URL, headers=headers).json()["items"]

    assert set(item) == {
        "username",
        "display_name",
        "bio",
        "avatar_key",
        "profile_visibility",
        "created_at",
        "last_seen_at",
    }
