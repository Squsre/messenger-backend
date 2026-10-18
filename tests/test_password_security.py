"""Password change and password reset tests."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import VerificationPurpose, VerificationToken
from tests.conftest import STRONG_PASSWORD

CHANGE_URL = "/api/v1/auth/password/change"
RESET_REQUEST_URL = "/api/v1/auth/password/reset/request"
RESET_CONFIRM_URL = "/api/v1/auth/password/reset/confirm"
SESSIONS_URL = "/api/v1/auth/sessions"
LOGIN_URL = "/api/v1/auth/login"

NEW_PASSWORD = "Ev3n-Str0nger-Pass!"
RESET_MESSAGE = "If that email address belongs to an account, a password reset link has been sent."


def _login(client, email: str, password: str = STRONG_PASSWORD):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


# --- changing the password --------------------------------------------------


def test_change_password_returns_a_working_replacement_token(client, api) -> None:
    api.register("alice@example.com")
    old_token = api.login("alice@example.com")
    old_headers = {"Authorization": f"Bearer {old_token}"}

    response = client.post(
        CHANGE_URL,
        json={"current_password": STRONG_PASSWORD, "new_password": NEW_PASSWORD},
        headers=old_headers,
    )

    assert response.status_code == 200
    replacement = response.json()["access_token"]
    assert replacement != old_token

    # The replacement works, the previous token does not.
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {replacement}"}).status_code == 200
    assert client.get("/api/v1/users/me", headers=old_headers).status_code == 401


def test_change_password_updates_the_credential(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    client.post(
        CHANGE_URL,
        json={"current_password": STRONG_PASSWORD, "new_password": NEW_PASSWORD},
        headers=headers,
    )

    assert _login(client, "alice@example.com", STRONG_PASSWORD).status_code == 401
    assert _login(client, "alice@example.com", NEW_PASSWORD).status_code == 200


def test_change_password_requires_the_current_password(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.post(
        CHANGE_URL,
        json={"current_password": "Wr0ng-Passw0rd!", "new_password": NEW_PASSWORD},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    # Nothing changed.
    assert _login(client, "alice@example.com", STRONG_PASSWORD).status_code == 200


def test_change_password_rejects_a_weak_replacement(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.post(
        CHANGE_URL,
        json={"current_password": STRONG_PASSWORD, "new_password": "weakpassword"},
        headers=headers,
    )

    assert response.status_code == 422
    assert "weakpassword" not in response.text
    assert _login(client, "alice@example.com", STRONG_PASSWORD).status_code == 200


def test_change_password_rejects_reusing_the_current_password(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.post(
        CHANGE_URL,
        json={"current_password": STRONG_PASSWORD, "new_password": STRONG_PASSWORD},
        headers=headers,
    )

    assert response.status_code == 400


def test_change_password_requires_authentication(client) -> None:
    response = client.post(
        CHANGE_URL,
        json={"current_password": STRONG_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 401


def test_change_password_ends_the_other_sessions(client, api) -> None:
    api.register("alice@example.com")
    first_token = api.login("alice@example.com")
    second_token = api.login("alice@example.com")
    first_headers = {"Authorization": f"Bearer {first_token}"}

    assert client.get(SESSIONS_URL, headers=first_headers).json()["total"] == 2

    response = client.post(
        CHANGE_URL,
        json={"current_password": STRONG_PASSWORD, "new_password": NEW_PASSWORD},
        headers=first_headers,
    )
    replacement = response.json()["access_token"]

    # The second device is gone; the device that changed the password survives.
    assert (
        client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {second_token}"}
        ).status_code
        == 401
    )
    sessions = client.get(
        SESSIONS_URL, headers={"Authorization": f"Bearer {replacement}"}
    ).json()
    assert sessions["total"] == 1
    assert sessions["items"][0]["current"] is True


def test_password_change_does_not_leak_the_submitted_values(client, api) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.post(
        CHANGE_URL,
        json={"current_password": STRONG_PASSWORD, "new_password": "short"},
        headers=headers,
    )

    assert response.status_code == 422
    assert STRONG_PASSWORD not in response.text
    assert "short" not in response.json()["error"]["message"]


# --- requesting a reset -----------------------------------------------------


def test_reset_request_answer_is_identical_for_unknown_addresses(client, api, outbox) -> None:
    api.register("alice@example.com")
    outbox.reset()

    unknown = client.post(RESET_REQUEST_URL, json={"email": "nobody@example.com"})
    known = client.post(RESET_REQUEST_URL, json={"email": "alice@example.com"})

    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json() == {"message": RESET_MESSAGE}

    # Only the registered address actually receives a message.
    assert len(outbox.messages) == 1
    assert outbox.last.to == "alice@example.com"


def test_reset_request_never_returns_the_token(client, api, outbox) -> None:
    api.register("alice@example.com")

    response = client.post(RESET_REQUEST_URL, json={"email": "alice@example.com"})

    assert outbox.last_token not in response.text


def test_reset_request_ignores_deactivated_accounts(client, api, outbox, db_session) -> None:
    from tests.conftest import deactivate_user

    api.register("alice@example.com")
    deactivate_user(db_session, "alice@example.com")
    outbox.reset()

    response = client.post(RESET_REQUEST_URL, json={"email": "alice@example.com"})

    assert response.status_code == 200
    assert response.json() == {"message": RESET_MESSAGE}
    assert outbox.messages == []


def test_reset_request_is_rate_limited(client, api, outbox) -> None:
    """The default budget is five requests per address per window."""
    for index in range(5):
        response = client.post(RESET_REQUEST_URL, json={"email": "nobody@example.com"})
        assert response.status_code == 200, index

    blocked = client.post(RESET_REQUEST_URL, json={"email": "nobody@example.com"})

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


def test_reset_request_rejects_a_malformed_address(client) -> None:
    assert client.post(RESET_REQUEST_URL, json={"email": "not-an-email"}).status_code == 422


# --- completing a reset -----------------------------------------------------


def test_reset_confirm_sets_the_new_password(client, api, outbox) -> None:
    api.register("alice@example.com")
    old_token = api.login("alice@example.com")
    client.post(RESET_REQUEST_URL, json={"email": "alice@example.com"})
    token = outbox.last_token

    response = client.post(
        RESET_CONFIRM_URL,
        json={"token": token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    assert _login(client, "alice@example.com", STRONG_PASSWORD).status_code == 401
    assert _login(client, "alice@example.com", NEW_PASSWORD).status_code == 200

    # Sessions that existed before the reset are invalidated.
    assert (
        client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {old_token}"}).status_code
        == 401
    )


def test_reset_token_is_single_use(client, api, outbox) -> None:
    api.register("alice@example.com")
    client.post(RESET_REQUEST_URL, json={"email": "alice@example.com"})
    token = outbox.last_token

    assert (
        client.post(RESET_CONFIRM_URL, json={"token": token, "new_password": NEW_PASSWORD}).status_code
        == 200
    )

    reused = client.post(
        RESET_CONFIRM_URL,
        json={"token": token, "new_password": "An0ther-Str0ng-Pass!"},
    )
    assert reused.status_code == 400
    # The original reset is untouched by the replay.
    assert _login(client, "alice@example.com", NEW_PASSWORD).status_code == 200


def test_reset_confirm_rejects_a_weak_password(client, api, outbox) -> None:
    api.register("alice@example.com")
    client.post(RESET_REQUEST_URL, json={"email": "alice@example.com"})
    token = outbox.last_token

    response = client.post(
        RESET_CONFIRM_URL,
        json={"token": token, "new_password": "weakpassword"},
    )

    assert response.status_code == 422
    assert "weakpassword" not in response.text
    # The password was not changed.
    assert _login(client, "alice@example.com", STRONG_PASSWORD).status_code == 200


def test_reset_confirm_rejects_an_unknown_token(client) -> None:
    response = client.post(
        RESET_CONFIRM_URL,
        json={"token": "z" * 43, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "This link is invalid or has expired."


def test_email_verification_token_cannot_be_used_to_reset_a_password(
    client, api, outbox
) -> None:
    """Tokens are bound to their purpose."""
    api.register("alice@example.com")
    verification_token = outbox.last_token

    response = client.post(
        RESET_CONFIRM_URL,
        json={"token": verification_token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400
    assert _login(client, "alice@example.com", STRONG_PASSWORD).status_code == 200


def test_reset_password_token_is_stored_hashed(client, api, outbox, db_session) -> None:
    api.register("alice@example.com")
    client.post(RESET_REQUEST_URL, json={"email": "alice@example.com"})
    raw_token = outbox.last_token

    row = db_session.execute(
        select(VerificationToken).where(
            VerificationToken.purpose == VerificationPurpose.PASSWORD_RESET.value
        )
    ).scalars().first()

    assert row is not None
    assert row.token_hash != raw_token
    assert raw_token not in repr(row)
