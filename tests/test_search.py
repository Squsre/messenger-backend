"""User search tests: ranking, paging, privacy and block filtering."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import User
from tests.conftest import deactivate_user

SEARCH_URL = "/api/v1/users/search"
ME_URL = "/api/v1/users/me"


def _search(client, headers, query: str, **params) -> dict:
    response = client.get(SEARCH_URL, params={"q": query, **params}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_exact_match_ranks_before_prefix_matches(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("alanx@example.com")
    api.register("alanis@example.com")
    api.register("alan@example.com")

    body = _search(client, headers, "alan")

    # Exact match first, the rest alphabetically.
    assert [item["username"] for item in body["items"]] == ["alan", "alanis", "alanx"]


def test_prefix_search_finds_users_by_partial_username(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("alice@example.com")
    api.register("alicia@example.com")

    found = {item["username"] for item in _search(client, headers, "ali")["items"]}

    assert found == {"alice", "alicia"}


def test_search_is_case_insensitive(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("alice@example.com")

    assert _search(client, headers, "ALI")["items"][0]["username"] == "alice"


def test_query_shorter_than_the_minimum_is_rejected(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")

    response = client.get(SEARCH_URL, params={"q": "a"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_empty_query_is_rejected(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")

    assert client.get(SEARCH_URL, params={"q": ""}, headers=headers).status_code == 422
    assert client.get(SEARCH_URL, headers=headers).status_code == 422


def test_search_results_are_paginated(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    for index in range(5):
        api.register(f"member{index}@example.com")

    first_page = _search(client, headers, "member", limit=2, offset=0)
    second_page = _search(client, headers, "member", limit=2, offset=2)

    assert first_page["total"] == 5
    assert first_page["limit"] == 2
    assert first_page["offset"] == 0
    assert len(first_page["items"]) == 2
    assert len(second_page["items"]) == 2
    assert {item["username"] for item in first_page["items"]} & {
        item["username"] for item in second_page["items"]
    } == set()


def test_limit_is_capped_by_the_server(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("member@example.com")

    body = _search(client, headers, "member", limit=100)

    assert body["limit"] == client.app.state.settings.search_max_limit


def test_search_excludes_the_caller(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")

    assert _search(client, headers, "seeker")["items"] == []


def test_search_excludes_deactivated_accounts(client, api, db_session) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("bob@example.com")
    deactivate_user(db_session, "bob@example.com")

    assert _search(client, headers, "bob")["items"] == []


def test_search_excludes_private_profiles(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    client.patch(
        "/api/v1/users/me/privacy",
        json={"profile_visibility": "private"},
        headers=alice_headers,
    )

    assert _search(client, headers, "alice")["items"] == []


def test_search_includes_contacts_only_profiles_without_leaking_details(client, api) -> None:
    """A ``contacts`` profile is still discoverable, but only with public fields."""
    _, headers = api.create_user("seeker@example.com")
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    client.patch(
        "/api/v1/users/me/privacy",
        json={"profile_visibility": "contacts", "show_last_seen": False},
        headers=alice_headers,
    )

    (item,) = _search(client, headers, "alice")["items"]

    assert item["username"] == "alice"
    assert item["last_seen_at"] is None
    for private_field in ("email", "id", "is_active", "email_verified", "show_last_seen"):
        assert private_field not in item


def test_search_never_returns_credentials(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("alice@example.com")

    response = client.get(SEARCH_URL, params={"q": "alice"}, headers=headers)

    assert "hashed_password" not in response.text
    assert "argon2" not in response.text
    assert "alice@example.com" not in response.text


def test_blocked_users_are_hidden_from_search_in_both_directions(client, api) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    assert _search(client, alice_headers, "bob")["total"] == 1

    client.post("/api/v1/blocks", json={"username": "bob"}, headers=alice_headers)

    assert _search(client, alice_headers, "bob")["items"] == []
    assert _search(client, bob_headers, "alice")["items"] == []


def test_search_requires_authentication(client) -> None:
    assert client.get(SEARCH_URL, params={"q": "alice"}).status_code == 401


def test_disabled_account_cannot_search(client, api, db_session) -> None:
    _, headers = api.create_user("seeker@example.com")
    deactivate_user(db_session, "seeker@example.com")

    assert client.get(SEARCH_URL, params={"q": "alice"}, headers=headers).status_code == 403


def test_search_is_rate_limited(outbox, api, client) -> None:
    """The default budget is 60 searches per minute per account."""
    api.register("seeker@example.com")
    headers = api.headers("seeker@example.com")
    attempts = client.app.state.settings.search_rate_limit_attempts

    for _ in range(attempts):
        assert client.get(SEARCH_URL, params={"q": "ab"}, headers=headers).status_code == 200

    blocked = client.get(SEARCH_URL, params={"q": "ab"}, headers=headers)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


def test_search_uses_the_username_index_range_not_a_table_scan(client, api) -> None:
    """The prefix filter compiles to an indexed range comparison.

    ``LIKE 'ab%'`` cannot use the plain b-tree index on PostgreSQL, so the query
    is built as ``username >= 'ab' AND username < 'ab\\uffff'``. This asserts the
    shape of the compiled SQL rather than guessing at timings.
    """
    from app.db.models import User as UserModel
    from app.services.contact import _search_conditions
    from sqlalchemy import select as sa_select

    statement = sa_select(UserModel.id).where(*_search_conditions("ab", viewer_id=1))
    sql = str(statement.compile(compile_kwargs={"literal_binds": False}))

    assert "users.username >=" in sql
    assert "users.username <" in sql
    assert " LIKE " not in sql.upper()


def test_total_counts_every_match_not_just_the_page(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    for index in range(4):
        api.register(f"member{index}@example.com")

    body = _search(client, headers, "member", limit=1)

    assert body["total"] == 4
    assert len(body["items"]) == 1


def test_search_ordering_is_stable_across_pages(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    for name in ("alpha", "alphas", "alphanet", "alph"):
        api.register(f"{name}@example.com")

    page_one = _search(client, headers, "alph", limit=2, offset=0)
    page_two = _search(client, headers, "alph", limit=2, offset=2)
    combined = [item["username"] for item in page_one["items"]] + [
        item["username"] for item in page_two["items"]
    ]

    assert combined == sorted(combined)
    assert len(combined) == 4


def test_last_seen_is_only_exposed_when_allowed(client, api) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    shared = _search(client, bob_headers, "alice")["items"][0]
    assert shared["last_seen_at"] is not None

    client.patch(
        "/api/v1/users/me/privacy", json={"show_last_seen": False}, headers=alice_headers
    )

    hidden = _search(client, bob_headers, "alice")["items"][0]
    assert hidden["last_seen_at"] is None


def test_search_only_matches_usernames_not_emails(client, api, db_session) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("hidden@example.com")
    # Give the account a username unrelated to its address.
    account = db_session.execute(
        select(User).where(User.email == "hidden@example.com")
    ).scalar_one()
    account.username = "wallflower"
    db_session.commit()

    assert _search(client, headers, "hidden")["items"] == []
    assert _search(client, headers, "wallflower")["items"][0]["username"] == "wallflower"


def test_search_item_contains_only_public_fields(client, api) -> None:
    _, headers = api.create_user("seeker@example.com")
    api.register("alice@example.com")

    (item,) = _search(client, headers, "alice")["items"]

    assert set(item) == {
        "username",
        "display_name",
        "bio",
        "avatar_key",
        "profile_visibility",
        "created_at",
        "last_seen_at",
    }
    # ``created_at`` is a real timestamp, not a placeholder. PostgreSQL returns
    # it with a UTC offset; normalise before comparing.
    parsed = datetime.fromisoformat(item["created_at"])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert abs((now - parsed).total_seconds()) < 300
