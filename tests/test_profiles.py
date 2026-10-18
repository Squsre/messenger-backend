"""Profile API tests: reads, edits, protected fields, privacy and avatars."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import User
from tests.conftest import deactivate_user

ME_URL = "/api/v1/users/me"
PRIVACY_URL = "/api/v1/users/me/privacy"
SEARCH_URL = "/api/v1/users/search"

#: Every field a user must not be able to write through the profile API.
PROTECTED_FIELDS = {
    "id": 999,
    "email": "attacker@example.com",
    "is_active": False,
    "hashed_password": "$argon2id$fake",
    "token_version": 5,
    "avatar_key": "avatars/1/0123456789abcdef0123456789abcdef.png",
    "email_verified": True,
    "created_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-01-01T00:00:00Z",
    "last_seen_at": "2020-01-01T00:00:00Z",
}


def _profile_url(username: str) -> str:
    return f"/api/v1/users/{username}"


# --- reading ----------------------------------------------------------------


def test_get_my_profile_returns_private_view(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    body = client.get(ME_URL, headers=headers).json()

    assert body["email"] == "alice@example.com"
    assert body["username"] == "alice"
    assert body["display_name"] is None
    assert body["profile_visibility"] == "public"
    assert body["show_last_seen"] is True
    assert body["is_active"] is True
    assert body["email_verified"] is False
    assert body["last_seen_at"] is not None


@pytest.mark.parametrize("field", ["hashed_password", "password", "token_version"])
def test_profile_never_exposes_credentials(client, api, field: str) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    assert field not in client.get(ME_URL, headers=headers).json()


def test_profile_endpoints_require_authentication(client, api) -> None:
    api.register("alice@example.com")

    assert client.get(ME_URL).status_code == 401
    assert client.patch(ME_URL, json={"bio": "hi"}).status_code == 401
    assert client.get(_profile_url("alice")).status_code == 401
    assert client.patch(PRIVACY_URL, json={"show_last_seen": False}).status_code == 401


def test_public_profile_hides_private_fields(client, api) -> None:
    api.register("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    body = client.get(_profile_url("alice"), headers=bob_headers).json()

    assert body["username"] == "alice"
    assert body["display_name"] == "alice"  # falls back to the username
    for private_field in ("email", "is_active", "email_verified", "show_last_seen", "id"):
        assert private_field not in body


def test_unknown_username_is_not_found(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    response = client.get(_profile_url("nobody"), headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_username_lookup_is_case_insensitive(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    assert client.get(_profile_url("ALICE"), headers=headers).status_code == 200


def test_deactivated_account_cannot_use_protected_routes(client, api, db_session) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    deactivate_user(db_session, "alice@example.com")

    assert client.get(ME_URL, headers=headers).status_code == 403


def test_deactivated_account_is_invisible_to_others(client, api, db_session) -> None:
    api.register("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    deactivate_user(db_session, "alice@example.com")

    assert client.get(_profile_url("alice"), headers=bob_headers).status_code == 404


# --- editing ----------------------------------------------------------------


def test_update_profile_fields(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    body = api.set_profile(
        headers,
        display_name="Alice A.",
        bio="Building things.",
        show_last_seen=False,
    )

    assert body["display_name"] == "Alice A."
    assert body["bio"] == "Building things."
    assert body["show_last_seen"] is False
    assert body["username"] == "alice"


def test_update_username_is_normalised_to_lowercase(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    body = api.set_profile(headers, username="AliceTheGreat")

    assert body["username"] == "alicethegreat"
    assert client.get(_profile_url("alicethegreat"), headers=headers).status_code == 200


def test_update_username_can_keep_the_current_value(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    assert api.set_profile(headers, username="alice")["username"] == "alice"


@pytest.mark.parametrize(
    ("username", "reason"),
    [
        ("Bob", "case-insensitive duplicate"),
        ("BOB", "case-insensitive duplicate"),
        ("bob", "exact duplicate"),
    ],
)
def test_duplicate_username_is_rejected_regardless_of_case(
    client, api, username: str, reason: str
) -> None:
    api.register("alice@example.com")
    api.register("bob@example.com")
    alice_headers = api.headers("alice@example.com")

    response = client.patch(ME_URL, json={"username": username}, headers=alice_headers)

    assert response.status_code == 409, reason
    assert response.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize(
    "username",
    [
        "ab",  # too short
        "a" * 33,  # too long
        "has space",
        "has-dash",
        "UPPER!",
        "dot.name",
        "admin",  # reserved
        "me",  # reserved (route collision)
        "search",  # reserved
        "_leading",
    ],
)
def test_invalid_usernames_are_rejected(client, api, username: str) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.patch(ME_URL, json={"username": username}, headers=headers)

    assert response.status_code == 422
    # The submitted value must not be echoed back with the error.
    details = response.json()["error"]["details"]
    assert details
    for detail in details:
        assert set(detail) <= {"type", "loc", "msg"}, detail


def test_username_cannot_be_cleared(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.patch(ME_URL, json={"username": None}, headers=headers)

    assert response.status_code == 422


def test_empty_string_clears_optional_fields(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    api.set_profile(headers, display_name="Alice", bio="Hello")

    body = api.set_profile(headers, display_name="", bio="  ")

    assert body["display_name"] is None
    assert body["bio"] is None


def test_over_long_values_are_rejected(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    assert client.patch(ME_URL, json={"bio": "x" * 281}, headers=headers).status_code == 422
    assert (
        client.patch(ME_URL, json={"display_name": "x" * 65}, headers=headers).status_code == 422
    )


def test_empty_payload_is_rejected(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    assert client.patch(ME_URL, json={}, headers=headers).status_code == 422


@pytest.mark.parametrize("field,value", sorted(PROTECTED_FIELDS.items()))
def test_protected_fields_cannot_be_written(client, api, field: str, value: object) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.patch(ME_URL, json={field: value}, headers=headers)

    assert response.status_code == 422, field
    assert response.json()["error"]["code"] == "validation_error"


def test_profile_edit_cannot_touch_another_account(client, api) -> None:
    api.register("alice@example.com")
    api.register("bob@example.com")
    alice_headers = api.headers("alice@example.com")

    api.set_profile(alice_headers, bio="alice only")

    bob_headers = api.headers("bob@example.com")
    bob_view = client.get(ME_URL, headers=bob_headers).json()
    assert bob_view["bio"] is None
    assert bob_view["username"] == "bob"


# --- privacy ----------------------------------------------------------------


def test_private_profile_is_hidden_from_others_but_visible_to_self(client, api) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    client.patch(PRIVACY_URL, json={"profile_visibility": "private"}, headers=alice_headers)

    assert client.get(_profile_url("alice"), headers=bob_headers).status_code == 404
    assert client.get(_profile_url("alice"), headers=alice_headers).status_code == 200


def test_contacts_only_profile_requires_a_contact_relationship(client, api) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    client.patch(PRIVACY_URL, json={"profile_visibility": "contacts"}, headers=alice_headers)

    assert client.get(_profile_url("alice"), headers=bob_headers).status_code == 404

    # Bob adds Alice: the relationship is what unlocks the profile.
    assert (
        client.post("/api/v1/contacts", json={"username": "alice"}, headers=bob_headers).status_code
        == 201
    )
    assert client.get(_profile_url("alice"), headers=bob_headers).status_code == 200


def test_invalid_visibility_is_rejected(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.patch(PRIVACY_URL, json={"profile_visibility": "friends"}, headers=headers)

    assert response.status_code == 422


def test_privacy_endpoint_requires_at_least_one_setting(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    assert client.patch(PRIVACY_URL, json={}, headers=headers).status_code == 422


def test_show_last_seen_false_hides_activity_from_others(client, api) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    assert client.get(_profile_url("alice"), headers=bob_headers).json()["last_seen_at"] is not None

    client.patch(PRIVACY_URL, json={"show_last_seen": False}, headers=alice_headers)

    assert client.get(_profile_url("alice"), headers=bob_headers).json()["last_seen_at"] is None
    # The owner always sees their own activity.
    assert client.get(ME_URL, headers=alice_headers).json()["last_seen_at"] is not None


def test_private_account_is_excluded_from_search(client, api) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    client.patch(PRIVACY_URL, json={"profile_visibility": "private"}, headers=alice_headers)

    found = client.get(SEARCH_URL, params={"q": "alice"}, headers=bob_headers).json()
    assert found["items"] == []


# --- avatars ----------------------------------------------------------------


def test_avatar_key_is_not_user_settable(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.patch(
        ME_URL,
        json={"avatar_key": "https://evil.example.com/tracker.png"},
        headers=headers,
    )

    assert response.status_code == 422
    assert client.get(ME_URL, headers=headers).json()["avatar_key"] is None


def test_avatar_key_set_by_the_storage_layer_is_returned(client, api, db_session) -> None:
    """The upload service (a later phase) is the only writer of this field."""
    from app.services.profile import set_avatar_key

    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    user = db_session.execute(select(User).where(User.email == "alice@example.com")).scalar_one()
    set_avatar_key(db_session, user, "avatars/1/0123456789abcdef0123456789abcdef.png")
    db_session.commit()

    key = "avatars/1/0123456789abcdef0123456789abcdef.png"
    assert client.get(ME_URL, headers=headers).json()["avatar_key"] == key
    assert client.get(_profile_url("alice"), headers=headers).json()["avatar_key"] == key


def test_invalid_avatar_key_is_refused_by_the_service(client, api, db_session) -> None:
    from app.services.profile import set_avatar_key

    api.register("alice@example.com")
    user = db_session.execute(select(User).where(User.email == "alice@example.com")).scalar_one()

    for bad_key in (
        "https://evil.example.com/x.png",
        "avatars/1/../../etc/passwd",
        "avatars/1/0123456789abcdef0123456789abcdef.exe",
    ):
        with pytest.raises(ValueError):
            set_avatar_key(db_session, user, bad_key)
