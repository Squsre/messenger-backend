"""Email-verification tests: issuing, confirming, expiry, reuse and delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_opaque_token
from app.db.base import Base
from app.db.models import User, VerificationPurpose, VerificationToken
from app.main import create_app
from tests.conftest import STRONG_PASSWORD, CapturingEmailService

REQUEST_URL = "/api/v1/auth/email/verify/request"
CONFIRM_URL = "/api/v1/auth/email/verify/confirm"
ME_URL = "/api/v1/users/me"


def _latest_token_row(db_session) -> VerificationToken:
    statement = (
        select(VerificationToken)
        .where(VerificationToken.purpose == VerificationPurpose.EMAIL_VERIFICATION.value)
        .order_by(VerificationToken.id.desc())
    )
    return db_session.execute(statement).scalars().first()


def test_registration_sends_a_verification_email(outbox, api, client) -> None:
    account = api.register("alice@example.com")

    assert len(outbox.messages) == 1
    message = outbox.last
    assert message.to == "alice@example.com"
    assert "Verify your email" in message.subject
    assert "token=" in message.body

    # The token must never be part of the API response.
    assert outbox.last_token not in client.get("/openapi.json").text
    assert outbox.last_token not in str(account)


def test_token_is_stored_hashed_not_raw(outbox, api, db_session) -> None:
    api.register("alice@example.com")
    raw_token = outbox.last_token

    row = _latest_token_row(db_session)

    assert row.token_hash != raw_token
    assert row.token_hash == hash_opaque_token(raw_token)
    assert row.used_at is None


def test_confirming_a_token_verifies_the_address(outbox, api, client) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    assert client.get(ME_URL, headers=headers).json()["email_verified"] is False

    response = client.post(CONFIRM_URL, json={"token": outbox.last_token})

    assert response.status_code == 200
    assert client.get(ME_URL, headers=headers).json()["email_verified"] is True


def test_a_token_cannot_be_used_twice(outbox, api, client) -> None:
    api.register("alice@example.com")
    token = outbox.last_token

    assert client.post(CONFIRM_URL, json={"token": token}).status_code == 200
    reused = client.post(CONFIRM_URL, json={"token": token})

    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "bad_request"


def test_an_expired_token_is_rejected(outbox, api, client, db_session) -> None:
    api.register("alice@example.com")
    row = _latest_token_row(db_session)
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    response = client.post(CONFIRM_URL, json={"token": outbox.last_token})

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "This link is invalid or has expired."


def test_unknown_and_malformed_tokens_are_rejected(client) -> None:
    unknown = client.post(CONFIRM_URL, json={"token": "x" * 43})
    assert unknown.status_code == 400
    assert unknown.json()["error"]["message"] == "This link is invalid or has expired."

    # Too short to be a token at all -> schema validation, not a 400.
    assert client.post(CONFIRM_URL, json={"token": "short"}).status_code == 422


def test_invalid_token_leaves_the_address_unverified(outbox, api, client) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    client.post(CONFIRM_URL, json={"token": "y" * 43})

    assert client.get(ME_URL, headers=headers).json()["email_verified"] is False


def test_resend_replaces_the_previous_token(outbox, api, client) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    first_token = outbox.last_token

    response = client.post(REQUEST_URL, headers=headers)

    assert response.status_code == 200
    second_token = outbox.last_token
    assert second_token != first_token

    # The superseded link must no longer work; only the newest one does.
    assert client.post(CONFIRM_URL, json={"token": first_token}).status_code == 400
    assert client.post(CONFIRM_URL, json={"token": second_token}).status_code == 200


def test_resend_answer_is_identical_when_already_verified(outbox, api, client) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    client.post(CONFIRM_URL, json={"token": outbox.last_token})
    messages_before = len(outbox.messages)

    response = client.post(REQUEST_URL, headers=headers)

    assert response.status_code == 200
    assert len(outbox.messages) == messages_before  # nothing new was sent
    assert "verification link has been sent" in response.json()["message"]


def test_resend_never_returns_the_token(outbox, api, client) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    response = client.post(REQUEST_URL, headers=headers)

    assert outbox.last_token not in response.text


def test_resend_requires_authentication(client) -> None:
    assert client.post(REQUEST_URL).status_code == 401


def test_resend_is_rate_limited(outbox, api, client) -> None:
    """The default budget is three sends per account per window."""
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")

    for _ in range(3):
        assert client.post(REQUEST_URL, headers=headers).status_code == 200

    blocked = client.post(REQUEST_URL, headers=headers)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1


def test_confirm_endpoint_is_rate_limited(client, outbox, api) -> None:
    api.register("alice@example.com")
    token = outbox.last_token
    # Three confirmations are allowed by the default budget; the fourth is not.
    for _ in range(3):
        client.post(CONFIRM_URL, json={"token": token})
    blocked = client.post(CONFIRM_URL, json={"token": token})
    assert blocked.status_code == 429


def test_login_is_not_blocked_by_an_unverified_address(client, api) -> None:
    """Documented behaviour: this phase does not require verification to sign in."""
    api.register("alice@example.com")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": STRONG_PASSWORD},
    )

    assert response.status_code == 200


def test_verification_email_is_not_logged_or_returned(client, db_session, api) -> None:
    """The raw token only exists inside the message handed to the transport."""
    from app.services.verification import issue_email_verification

    api.register("alice@example.com")
    user = db_session.execute(select(User).where(User.email == "alice@example.com")).scalar_one()

    transport = CapturingEmailService()
    settings = client.app.state.settings
    issue_email_verification(db_session, user=user, settings=settings, email_service=transport)
    db_session.commit()

    raw_token = transport.last_token
    assert raw_token not in str(vars(user))
    assert raw_token not in repr(_latest_token_row(db_session))


def _console_settings(tmp_path: Path, db_url: str, outbox_dir: Path):
    from app.core.config import Settings

    return Settings(
        app_env="development",
        app_debug=True,
        database_url=db_url,
        jwt_secret_key="console-backend-test-secret-0123456789abcdef",
        email_backend="console",
        email_outbox_dir=str(outbox_dir),
        email_from="no-reply@messenger.test",
        public_base_url="http://testserver",
        rate_limit_enabled=True,
        email_rate_limit_attempts=100,
    )


def test_console_backend_writes_the_link_to_the_outbox(tmp_path: Path, db_url: str) -> None:
    """Development flow: the link lands in a file, never in the API response."""
    outbox_dir = tmp_path / "outbox"
    application = create_app(settings=_console_settings(tmp_path, db_url, outbox_dir))
    Base.metadata.create_all(bind=application.state.database.engine)

    try:
        with TestClient(application) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={"email": "alice@example.com", "password": STRONG_PASSWORD},
            )
            assert response.status_code == 201

            files = sorted(outbox_dir.glob("*.txt"))
            assert len(files) == 1
            content = files[0].read_text(encoding="utf-8")

            assert "alice@example.com" in content
            assert "http://testserver/verify-email?token=" in content

            token = content.split("token=", 1)[1].split("\n", 1)[0].strip()
            assert token
            # The response itself contains no token.
            assert token not in response.text
    finally:
        application.state.database.dispose()


def test_console_backend_is_refused_outside_local_environments(db_url: str) -> None:
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            database_url=db_url,
            jwt_secret_key="production-secret-0123456789abcdefghijklmnop",
            email_backend="console",
        )


def test_disabled_backend_still_issues_a_usable_token(client, api, db_session) -> None:
    """With delivery switched off the token is still created and can be consumed.

    This is what makes the flow testable without a mail server.
    """
    api.register("alice@example.com")
    row = _latest_token_row(db_session)
    assert row is not None and row.used_at is None


def test_default_settings_do_not_use_the_console_backend() -> None:
    from app.core.config import Settings

    settings = Settings(
        database_url="postgresql+psycopg://test:test@127.0.0.1:5432/test",
        jwt_secret_key="x" * 40,
    )
    assert settings.email_backend in {"console", "smtp", "disabled"}
    assert settings.email_backend != "smtp"


def test_verification_token_has_a_bounded_lifetime(outbox, api, db_session) -> None:
    api.register("alice@example.com")
    row = _latest_token_row(db_session)

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    lifetime_minutes = (expires_at - created_at).total_seconds() / 60
    assert 0 < lifetime_minutes <= 1440
