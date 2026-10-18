"""Device-session tests: listing, revoking, logging out everywhere, JWTs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.security import create_access_token, decode_access_token
from app.db.models import User, UserSession
from tests.conftest import STRONG_PASSWORD

SESSIONS_URL = "/api/v1/auth/sessions"
LOGOUT_URL = "/api/v1/auth/logout"
LOGOUT_ALL_URL = "/api/v1/auth/logout/all"
ME_URL = "/api/v1/users/me"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _other_session_id(client, headers) -> int:
    """The id of the caller's session that is *not* the current one."""
    sessions = client.get(SESSIONS_URL, headers=headers).json()["items"]
    return next(session["id"] for session in sessions if not session["current"])


# --- listing ----------------------------------------------------------------


def test_login_creates_a_session_visible_to_its_owner(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    body = client.get(SESSIONS_URL, headers=headers).json()

    assert body["total"] == 1
    (session,) = body["items"]
    assert session["current"] is True
    assert session["expires_at"] > session["created_at"]
    assert session["ip_address"]


def test_each_login_adds_a_device(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    api.login("alice@example.com")

    body = client.get(SESSIONS_URL, headers=headers).json()

    assert body["total"] == 2
    assert sum(1 for session in body["items"] if session["current"]) == 1


def test_sessions_require_authentication(client) -> None:
    assert client.get(SESSIONS_URL).status_code == 401
    assert client.delete(f"{SESSIONS_URL}/1").status_code == 401
    assert client.post(LOGOUT_ALL_URL).status_code == 401


def test_session_listing_is_scoped_to_the_owner(client, api) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    api.login("alice@example.com")

    _, bob_headers = api.create_user("bob@example.com")

    assert client.get(SESSIONS_URL, headers=bob_headers).json()["total"] == 1
    assert client.get(SESSIONS_URL, headers=alice_headers).json()["total"] == 2


def test_user_agent_is_stored_bounded(client, api, db_session) -> None:
    api.register("alice@example.com")
    long_agent = "A" * 400
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": STRONG_PASSWORD},
        headers={"User-Agent": long_agent},
    )
    assert response.status_code == 200

    row = db_session.execute(select(UserSession).order_by(UserSession.id.desc())).scalars().first()

    assert row.user_agent is not None
    assert len(row.user_agent) == 255


def test_control_characters_are_stripped_from_the_user_agent(client, api, db_session) -> None:
    api.register("alice@example.com")
    client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": STRONG_PASSWORD},
        headers={"User-Agent": "Agent\r\nInjected: header"},
    )

    row = db_session.execute(select(UserSession).order_by(UserSession.id.desc())).scalars().first()

    assert row.user_agent is not None
    assert "\r" not in row.user_agent and "\n" not in row.user_agent


# --- secret handling --------------------------------------------------------


def test_no_token_or_secret_is_stored_in_the_session_table(client, api, db_session) -> None:
    api.register("alice@example.com")
    token = api.login("alice@example.com")
    claims = decode_access_token(token, settings=client.app.state.settings)

    row = db_session.execute(
        select(UserSession).where(UserSession.session_id == claims.jti)
    ).scalars().one()

    # The identifier matches the token's id; the token itself is nowhere.
    assert row.session_id == claims.jti
    assert token not in repr(row)
    assert token not in str(row.__dict__)
    assert token not in str(claims.jti) + str(row.user_id)


# --- revoking one device ----------------------------------------------------


def test_revoking_a_session_invalidates_only_that_token(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    other_token = api.login("alice@example.com")
    target_id = _other_session_id(client, headers)

    response = client.delete(f"{SESSIONS_URL}/{target_id}", headers=headers)

    assert response.status_code == 200
    assert client.get(ME_URL, headers=_bearer(other_token)).status_code == 401
    assert client.get(ME_URL, headers=headers).status_code == 200


def test_revoked_session_disappears_from_the_list(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    api.login("alice@example.com")
    target_id = _other_session_id(client, headers)

    client.delete(f"{SESSIONS_URL}/{target_id}", headers=headers)

    body = client.get(SESSIONS_URL, headers=headers).json()
    assert body["total"] == 1
    assert all(session["id"] != target_id for session in body["items"])


def test_another_users_session_cannot_be_seen_or_revoked(client, api) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    alice_session_id = client.get(SESSIONS_URL, headers=alice_headers).json()["items"][0]["id"]

    _, bob_headers = api.create_user("bob@example.com")

    # Bob cannot even learn that the session exists.
    assert client.delete(f"{SESSIONS_URL}/{alice_session_id}", headers=bob_headers).status_code == 404
    # Alice's session is untouched.
    assert client.get(ME_URL, headers=alice_headers).status_code == 200


def test_revoking_an_unknown_session_is_not_found(client, api) -> None:
    _, headers = api.create_user("alice@example.com")

    assert client.delete(f"{SESSIONS_URL}/999999", headers=headers).status_code == 404


def test_revoking_the_current_session_is_allowed(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    current_id = client.get(SESSIONS_URL, headers=headers).json()["items"][0]["id"]

    assert client.delete(f"{SESSIONS_URL}/{current_id}", headers=headers).status_code == 200
    assert client.get(ME_URL, headers=headers).status_code == 401


# --- logging out ------------------------------------------------------------


def test_logout_marks_the_session_revoked(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    client.post(LOGOUT_URL, headers=headers)

    assert client.get(SESSIONS_URL, headers=headers).status_code == 401


def test_logout_revokes_only_the_presented_token(client, api) -> None:
    api.register("alice@example.com")
    first = api.login("alice@example.com")
    second = api.login("alice@example.com")

    assert client.post(LOGOUT_URL, headers=_bearer(first)).status_code == 200

    assert client.get(ME_URL, headers=_bearer(first)).status_code == 401
    assert client.get(ME_URL, headers=_bearer(second)).status_code == 200


def test_logout_all_invalidates_every_token_and_session(client, api) -> None:
    api.register("alice@example.com")
    first = api.login("alice@example.com")
    second = api.login("alice@example.com")

    response = client.post(LOGOUT_ALL_URL, headers=_bearer(first))

    assert response.status_code == 200
    for token in (first, second):
        assert client.get(ME_URL, headers=_bearer(token)).status_code == 401


def test_logout_all_closes_every_session_row(client, api, db_session) -> None:
    api.register("alice@example.com")
    first = api.login("alice@example.com")
    api.login("alice@example.com")

    client.post(LOGOUT_ALL_URL, headers=_bearer(first))

    user_id = db_session.execute(
        select(User.id).where(User.email == "alice@example.com")
    ).scalar_one()
    rows = db_session.execute(
        select(UserSession).where(UserSession.user_id == user_id)
    ).scalars().all()

    assert rows
    assert all(row.revoked_at is not None for row in rows)


# --- compatibility with pre-existing tokens ---------------------------------


def test_a_token_without_a_session_row_still_works(client, api) -> None:
    """Tokens issued before device sessions existed must not break."""
    api.register("alice@example.com")

    with client.app.state.database.session() as session:
        account = session.execute(
            select(User).where(User.email == "alice@example.com")
        ).scalar_one()
        legacy_token, _ = create_access_token(
            account.id,
            settings=client.app.state.settings,
            version=account.token_version,
        )

    assert client.get(ME_URL, headers=_bearer(legacy_token)).status_code == 200


def test_bumping_the_token_version_invalidates_a_legacy_token(client, api, db_session) -> None:
    api.register("alice@example.com")

    account = db_session.execute(select(User).where(User.email == "alice@example.com")).scalar_one()
    legacy_token, _ = create_access_token(
        account.id,
        settings=client.app.state.settings,
        version=account.token_version,
    )
    assert client.get(ME_URL, headers=_bearer(legacy_token)).status_code == 200

    # Simulates "log out everywhere" for a token the denylist never saw.
    account.token_version += 1
    db_session.commit()

    assert client.get(ME_URL, headers=_bearer(legacy_token)).status_code == 401


def test_logout_all_also_kills_legacy_tokens(client, api, db_session) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    account = db_session.execute(select(User).where(User.email == "alice@example.com")).scalar_one()
    legacy_token, _ = create_access_token(
        account.id,
        settings=client.app.state.settings,
        version=account.token_version,
    )
    assert client.get(ME_URL, headers=_bearer(legacy_token)).status_code == 200

    client.post(LOGOUT_ALL_URL, headers=headers)

    assert client.get(ME_URL, headers=_bearer(legacy_token)).status_code == 401


def test_pruned_finished_sessions_do_not_grow_the_list(client, api, db_session) -> None:
    """A revoked, expired session is dropped instead of lingering forever."""
    from app.services.session import prune_finished_sessions, revoke_session

    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    row = db_session.execute(select(UserSession).order_by(UserSession.id.desc())).scalars().first()
    revoke_session(db_session, session=row)
    row.expires_at = datetime.now(timezone.utc) - datetime.resolution
    db_session.commit()

    assert prune_finished_sessions(db_session, user_id=row.user_id) == 1
    assert client.get(SESSIONS_URL, headers=headers).status_code == 401
