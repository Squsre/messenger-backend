"""End-to-end tests for the authentication API."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import create_access_token
from app.db.models import TokenBlocklist, User

STRONG_PASSWORD = "Str0ng-Passw0rd!"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"
LOGOUT_URL = "/api/v1/auth/logout"


# --- helpers ----------------------------------------------------------------


def register(client, email: str = "user@example.com", password: str = STRONG_PASSWORD):
    return client.post(REGISTER_URL, json={"email": email, "password": password})


def login(client, email: str = "user@example.com", password: str = STRONG_PASSWORD):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_and_login(client, email: str = "user@example.com") -> str:
    assert register(client, email).status_code == 201
    response = login(client, email)
    assert response.status_code == 200
    return response.json()["access_token"]


# --- registration -----------------------------------------------------------


def test_register_success(client) -> None:
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "user@example.com"
    assert body["is_active"] is True
    assert body["id"] > 0
    assert body["created_at"]
    # The password must never be echoed back, in any form.
    assert "password" not in body
    assert "hashed_password" not in body
    assert STRONG_PASSWORD not in response.text


def test_register_normalises_email_case(client, db_session) -> None:
    response = register(client, "User@Example.COM")

    assert response.status_code == 201
    assert response.json()["email"] == "user@example.com"
    stored = db_session.execute(select(User)).scalar_one()
    assert stored.email == "user@example.com"


def test_register_duplicate_email_is_rejected(client) -> None:
    assert register(client).status_code == 201

    response = register(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_register_duplicate_email_is_case_insensitive(client) -> None:
    assert register(client, "user@example.com").status_code == 201

    response = register(client, "USER@example.com")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize("email", ["not-an-email", "missing@tld", "", "a b@example.com"])
def test_register_rejects_invalid_email(client, email: str) -> None:
    response = client.post(REGISTER_URL, json={"email": email, "password": STRONG_PASSWORD})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_register_rejects_missing_fields(client) -> None:
    response = client.post(REGISTER_URL, json={"email": "user@example.com"})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "password",
    [
        "Short1!",  # too short
        "alllowercase1!",  # no upper case
        "ALLUPPERCASE1!",  # no lower case
        "NoDigitsHere!!",  # no digit
        "NoSpecial12345",  # no special character
    ],
)
def test_register_rejects_weak_password(client, password: str) -> None:
    response = client.post(REGISTER_URL, json={"email": "user@example.com", "password": password})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_register_rejects_overlong_password(client) -> None:
    response = client.post(
        REGISTER_URL, json={"email": "user@example.com", "password": "Aa1!" + "x" * 200}
    )

    assert response.status_code == 422


def test_validation_error_does_not_echo_the_password(client) -> None:
    """A rejected password must not be reflected back in the error payload."""
    weak = "Sup3rSecret!"
    response = client.post(REGISTER_URL, json={"email": "bad-email", "password": weak})

    assert response.status_code == 422
    assert weak not in response.text


# --- login ------------------------------------------------------------------


def test_login_success(client) -> None:
    assert register(client).status_code == 201

    response = login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["access_token"].count(".") == 2  # header.payload.signature


def test_login_success_is_case_insensitive_for_email(client) -> None:
    assert register(client, "user@example.com").status_code == 201

    assert login(client, "USER@Example.com").status_code == 200


def test_login_with_wrong_password_is_rejected(client) -> None:
    assert register(client).status_code == 201

    response = login(client, password="Wr0ng-Passw0rd!")

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Incorrect email or password."
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_with_unknown_email_is_rejected(client) -> None:
    response = login(client, email="nobody@example.com")

    assert response.status_code == 401


def test_login_errors_are_identical_for_unknown_email_and_wrong_password(client) -> None:
    assert register(client, "known@example.com").status_code == 201

    unknown = login(client, email="unknown@example.com")
    wrong = login(client, email="known@example.com", password="Wr0ng-Passw0rd!")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_login_for_disabled_user_is_forbidden(client, db_session) -> None:
    assert register(client).status_code == 201
    user = db_session.execute(select(User)).scalar_one()
    user.is_active = False
    db_session.commit()

    response = login(client)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


# --- /me --------------------------------------------------------------------


def test_me_with_valid_token(client) -> None:
    token = register_and_login(client)

    response = client.get(ME_URL, headers=bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "user@example.com"
    assert body["is_active"] is True


def test_me_without_token_is_unauthorized(client) -> None:
    response = client.get(ME_URL)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_with_malformed_token_is_unauthorized(client) -> None:
    response = client.get(ME_URL, headers=bearer("not.a.valid.jwt"))

    assert response.status_code == 401


def test_me_with_tampered_token_is_unauthorized(client) -> None:
    token = register_and_login(client)
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")

    response = client.get(ME_URL, headers=bearer(tampered))

    assert response.status_code == 401


def test_me_with_expired_token_is_unauthorized(client, test_settings: Settings, db_session) -> None:
    assert register(client).status_code == 201
    user = db_session.execute(select(User)).scalar_one()

    expired, _ = create_access_token(
        user.id, settings=test_settings, expires_delta=timedelta(seconds=-1)
    )

    response = client.get(ME_URL, headers=bearer(expired))

    assert response.status_code == 401


def test_me_with_token_signed_by_other_secret_is_unauthorized(client, db_session) -> None:
    assert register(client).status_code == 201
    user = db_session.execute(select(User)).scalar_one()

    foreign_settings = Settings(
        app_env="test",
        jwt_secret_key="a-totally-different-secret-value-0123456789",
    )
    forged, _ = create_access_token(user.id, settings=foreign_settings)

    response = client.get(ME_URL, headers=bearer(forged))

    assert response.status_code == 401


def test_me_with_token_for_missing_user_is_unauthorized(client, test_settings: Settings) -> None:
    token, _ = create_access_token(999_999, settings=test_settings)

    response = client.get(ME_URL, headers=bearer(token))

    assert response.status_code == 401


def test_me_for_disabled_user_is_forbidden(client, db_session) -> None:
    token = register_and_login(client)
    user = db_session.execute(select(User)).scalar_one()
    user.is_active = False
    db_session.commit()

    response = client.get(ME_URL, headers=bearer(token))

    assert response.status_code == 403


def test_me_with_non_bearer_scheme_is_unauthorized(client) -> None:
    response = client.get(ME_URL, headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert response.status_code == 401


# --- logout -----------------------------------------------------------------


def test_logout_revokes_the_token(client) -> None:
    token = register_and_login(client)

    logout_response = client.post(LOGOUT_URL, headers=bearer(token))
    assert logout_response.status_code == 200

    after = client.get(ME_URL, headers=bearer(token))
    assert after.status_code == 401
    assert after.json()["error"]["message"] == "Token has been revoked."


def test_logout_records_the_token_in_the_blocklist(client, db_session) -> None:
    token = register_and_login(client)

    assert client.post(LOGOUT_URL, headers=bearer(token)).status_code == 200

    revoked = db_session.execute(select(TokenBlocklist)).scalars().all()
    assert len(revoked) == 1
    assert revoked[0].token_type == "access"
    assert revoked[0].user_id is not None


def test_logout_does_not_affect_other_tokens(client) -> None:
    assert register(client).status_code == 201
    first = login(client).json()["access_token"]
    second = login(client).json()["access_token"]
    assert first != second

    assert client.post(LOGOUT_URL, headers=bearer(first)).status_code == 200

    assert client.get(ME_URL, headers=bearer(first)).status_code == 401
    assert client.get(ME_URL, headers=bearer(second)).status_code == 200


def test_logout_is_idempotent(client) -> None:
    token = register_and_login(client)

    assert client.post(LOGOUT_URL, headers=bearer(token)).status_code == 200
    assert client.post(LOGOUT_URL, headers=bearer(token)).status_code == 200


def test_logout_without_token_is_unauthorized(client) -> None:
    assert client.post(LOGOUT_URL).status_code == 401


def test_logout_with_expired_token_is_unauthorized(client, test_settings: Settings, db_session) -> None:
    assert register(client).status_code == 201
    user = db_session.execute(select(User)).scalar_one()
    expired, _ = create_access_token(
        user.id, settings=test_settings, expires_delta=timedelta(seconds=-1)
    )

    assert client.post(LOGOUT_URL, headers=bearer(expired)).status_code == 401


# --- storage guarantees -----------------------------------------------------


def test_raw_password_is_never_stored(client, db_session) -> None:
    assert register(client).status_code == 201

    user = db_session.execute(select(User)).scalar_one()
    assert user.hashed_password != STRONG_PASSWORD
    assert STRONG_PASSWORD not in user.hashed_password
    assert user.hashed_password.startswith("$argon2id$")


def test_only_one_column_holds_credential_material(db_session) -> None:
    """Guard against someone later adding a plaintext password column."""
    columns = {column.name for column in User.__table__.columns}
    assert "hashed_password" in columns
    assert "password" not in columns
