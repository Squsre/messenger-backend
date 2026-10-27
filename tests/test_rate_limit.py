"""Tests for the login rate limiter (unit + API behaviour)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from app.api import deps
from app.core.config import Settings
from app.core.errors import RateLimitError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.db.base import Base
from app.main import create_app

STRONG_PASSWORD = "Str0ng-Passw0rd!"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"


# --- unit -------------------------------------------------------------------


def test_limiter_allows_up_to_the_limit_then_denies() -> None:
    now = [0.0]
    limiter = SlidingWindowRateLimiter(3, 10, clock=lambda: now[0])

    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is True

    allowed, retry_after = limiter.check("k")
    assert allowed is False
    assert 0 < retry_after <= 10


def test_limiter_frees_the_slot_after_the_window() -> None:
    now = [0.0]
    limiter = SlidingWindowRateLimiter(1, 10, clock=lambda: now[0])

    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is False

    now[0] = 11.0
    assert limiter.check("k")[0] is True


def test_limiter_isolates_keys() -> None:
    limiter = SlidingWindowRateLimiter(1, 10, clock=lambda: 0.0)

    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True
    assert limiter.check("a")[0] is False


def test_limiter_reset() -> None:
    limiter = SlidingWindowRateLimiter(1, 10, clock=lambda: 0.0)

    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is False

    limiter.reset("k")
    assert limiter.check("k")[0] is True


# --- API --------------------------------------------------------------------


def _build_app(
    db_url: str,
    redis_url: str,
    redis_namespace: str,
    *,
    attempts: int,
    enabled: bool = True,
):
    settings = Settings(
        app_env="test",
        jwt_secret_key="rate-limit-test-secret-0123456789abcdefghij",
        database_url=db_url,
        redis_url=redis_url,
        redis_key_prefix=redis_namespace,
        rate_limit_enabled=enabled,
        login_rate_limit_attempts=attempts,
        login_rate_limit_window_seconds=60,
    )
    application = create_app(settings=settings)
    Base.metadata.create_all(bind=application.state.database.engine)
    return application


def test_login_is_rate_limited_by_client_ip(
    db_url: str, test_redis_url: str, redis_namespace: str
) -> None:
    app = _build_app(
        db_url, test_redis_url, redis_namespace, attempts=3
    )
    with TestClient(app) as client:
        client.post(REGISTER_URL, json={"email": "user@example.com", "password": STRONG_PASSWORD})

        # Three attempts are consumed by the failures below.
        for _ in range(3):
            assert client.post(
                LOGIN_URL, json={"email": "user@example.com", "password": "Wr0ng-Passw0rd!"}
            ).status_code == 401

        blocked = client.post(
            LOGIN_URL, json={"email": "user@example.com", "password": STRONG_PASSWORD}
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert int(blocked.headers["retry-after"]) >= 1

    app.state.redis_manager.close()
    app.state.database.dispose()


def test_rate_limit_is_shared_per_account_across_addresses(
    db_url: str, test_redis_url: str, redis_namespace: str
) -> None:
    """A distributed attempt on one account is throttled even from new IPs.

    Exercises the dependency directly: this Starlette version's ``TestClient``
    cannot vary the peer address, so we build requests with different clients.
    """
    app = _build_app(
        db_url, test_redis_url, redis_namespace, attempts=3
    )
    settings = app.state.settings
    email = "user@example.com"

    def request_from(host: str) -> StarletteRequest:
        return StarletteRequest(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/auth/login",
                "raw_path": b"/api/v1/auth/login",
                "query_string": b"",
                "headers": [],
                "client": (host, 43210),
                "server": ("testserver", 80),
                # ``request.app`` reads this entry.
                "app": app,
            }
        )

    # Three different source addresses each get their own (unexhausted) IP bucket.
    for index in range(3):
        deps.enforce_login_rate_limit(request_from(f"10.0.0.{index + 1}"), settings, email)

    # A fourth address is still refused: the account key is exhausted.
    with pytest.raises(RateLimitError) as excinfo:
        deps.enforce_login_rate_limit(request_from("10.0.0.99"), settings, email)

    assert excinfo.value.status_code == 429
    assert excinfo.value.headers and "Retry-After" in excinfo.value.headers

    app.state.redis_manager.close()
    app.state.database.dispose()


def test_rate_limit_can_be_disabled(
    db_url: str, test_redis_url: str, redis_namespace: str
) -> None:
    app = _build_app(
        db_url, test_redis_url, redis_namespace, attempts=1, enabled=False
    )
    with TestClient(app) as client:
        client.post(REGISTER_URL, json={"email": "user@example.com", "password": STRONG_PASSWORD})

        for _ in range(5):
            response = client.post(
                LOGIN_URL, json={"email": "user@example.com", "password": "Wr0ng-Passw0rd!"}
            )
            assert response.status_code == 401

    app.state.redis_manager.close()
    app.state.database.dispose()
