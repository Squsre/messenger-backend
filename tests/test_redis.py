"""Redis integration tests: cache, TTLs, invalidation and shared throttling."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.rate_limit import RateLimitUnavailable, RedisSlidingWindowRateLimiter
from app.db.base import Base
from app.main import create_app
from app.services.cache import CacheService
from app.services.profile_cache import profile_cache_key
from app.services.redis import redact_redis_url

STRONG_PASSWORD = "Str0ng-Passw0rd!"
LOGIN_URL = "/api/v1/auth/login"
PROFILE_URL = "/api/v1/users/alice"
ME_URL = "/api/v1/users/me"
PRIVACY_URL = "/api/v1/users/me/privacy"
READY_URL = "/api/v1/health/ready"


class _FailingClient:
    """A redis-py-shaped client whose operations all fail."""

    def get(self, *_args, **_kwargs):
        raise RedisError("simulated outage")

    def set(self, *_args, **_kwargs):
        raise RedisError("simulated outage")

    def delete(self, *_args, **_kwargs):
        raise RedisError("simulated outage")

    def eval(self, *_args, **_kwargs):
        raise RedisError("simulated outage")


class _FailingManager:
    def __init__(self) -> None:
        self.client = _FailingClient()

    def key(self, *parts: object) -> str:
        return "test:" + ":".join(str(part) for part in parts)


def test_redis_ping_and_redaction(app) -> None:
    assert app.state.redis_manager.ping() is True

    redacted = redact_redis_url("redis://:s3cret@example.test:6379/0")
    assert redacted == "redis://:***@example.test:6379/0"
    assert "s3cret" not in redacted


def test_redis_manager_closes_cleanly(app) -> None:
    manager = app.state.redis_manager
    assert manager.ping() is True
    manager.close()
    manager.close()  # shutdown is idempotent


def test_invalid_redis_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="redis_url must use one of"):
        Settings(
            app_env="test",
            database_url="postgresql+psycopg://user:pass@127.0.0.1:5432/test",
            jwt_secret_key="redis-url-test-secret-0123456789abcdef",
            redis_url="http://127.0.0.1:6379/0",
        )


def test_cache_round_trip_delete_and_ttl(app) -> None:
    cache: CacheService = app.state.cache
    assert cache.set("round-trip", "value", ttl_seconds=30) is True
    assert cache.get("round-trip") == "value"
    assert cache.delete("round-trip") is True
    assert cache.get("round-trip") is None

    assert cache.set("short-lived", "value", ttl_seconds=1) is True
    time.sleep(1.1)
    assert cache.get("short-lived") is None


def test_cache_fails_open_on_redis_errors() -> None:
    cache = CacheService(_FailingManager())
    assert cache.get("anything") is None
    assert cache.set("anything", "value", ttl_seconds=10) is False
    assert cache.delete("anything") is False


def test_public_profile_is_cached_and_invalidated_on_edit(client, api, app) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    first = client.get(PROFILE_URL, headers=bob_headers)
    assert first.status_code == 200
    cache = app.state.cache
    assert cache.get(profile_cache_key("alice")) is not None

    changed = client.patch(ME_URL, json={"bio": "fresh bio"}, headers=alice_headers)
    assert changed.status_code == 200
    assert cache.get(profile_cache_key("alice")) is None

    second = client.get(PROFILE_URL, headers=bob_headers)
    assert second.status_code == 200
    assert second.json()["bio"] == "fresh bio"


def test_cached_profile_payload_contains_only_public_fields(client, api, app) -> None:
    api.register("alice@example.com")
    _, viewer_headers = api.create_user("viewer@example.com")

    assert client.get(PROFILE_URL, headers=viewer_headers).status_code == 200
    payload = app.state.cache.get_json(profile_cache_key("alice"))
    assert isinstance(payload, dict)
    for private_field in (
        "id",
        "email",
        "hashed_password",
        "is_active",
        "token_version",
        "show_last_seen",
    ):
        assert private_field not in payload


def test_username_change_invalidates_both_cache_keys(client, api, app) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")

    assert client.get(PROFILE_URL, headers=bob_headers).status_code == 200
    cache = app.state.cache
    assert cache.get(profile_cache_key("alice")) is not None

    response = client.patch(
        ME_URL, json={"username": "alice_new"}, headers=alice_headers
    )
    assert response.status_code == 200
    assert cache.get(profile_cache_key("alice")) is None
    assert cache.get(profile_cache_key("alice_new")) is None

    assert client.get("/api/v1/users/alice_new", headers=bob_headers).status_code == 200
    assert cache.get(profile_cache_key("alice_new")) is not None


def test_restricted_or_hidden_profiles_are_not_cached(client, api, app) -> None:
    api.register("alice@example.com")
    alice_headers = api.headers("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    cache = app.state.cache

    response = client.patch(
        PRIVACY_URL, json={"show_last_seen": False}, headers=alice_headers
    )
    assert response.status_code == 200
    assert client.get(PROFILE_URL, headers=bob_headers).status_code == 200
    assert cache.get(profile_cache_key("alice")) is None

    response = client.patch(
        PRIVACY_URL, json={"profile_visibility": "private"}, headers=alice_headers
    )
    assert response.status_code == 200
    assert client.get(PROFILE_URL, headers=bob_headers).status_code == 404
    assert cache.get(profile_cache_key("alice")) is None


def test_cached_profile_still_honours_blocks(client, api, app) -> None:
    api.register("alice@example.com")
    _, bob_headers = api.create_user("bob@example.com")
    assert client.get(PROFILE_URL, headers=bob_headers).status_code == 200
    assert app.state.cache.get(profile_cache_key("alice")) is not None

    blocked = client.post(
        "/api/v1/blocks", json={"username": "alice"}, headers=bob_headers
    )
    assert blocked.status_code == 201
    assert client.get(PROFILE_URL, headers=bob_headers).status_code == 404


def test_cache_does_not_mix_users(client, api, app) -> None:
    api.register("alice@example.com")
    api.register("bob@example.com")
    _, viewer_headers = api.create_user("viewer@example.com")
    cache = app.state.cache

    alice = client.get(PROFILE_URL, headers=viewer_headers)
    bob = client.get("/api/v1/users/bob", headers=viewer_headers)
    assert alice.status_code == bob.status_code == 200
    assert alice.json()["username"] == "alice"
    assert bob.json()["username"] == "bob"
    assert cache.get(profile_cache_key("alice")) is not None
    assert cache.get(profile_cache_key("bob")) is not None


def test_rate_limit_is_shared_between_app_instances(
    test_settings, client, api
) -> None:
    """Two independent app objects share the same Redis counters."""
    settings = test_settings.model_copy(
        update={"login_rate_limit_attempts": 3, "login_rate_limit_window_seconds": 60}
    )
    second_app = create_app(settings=settings)
    Base.metadata.create_all(bind=second_app.state.database.engine)

    api.register("shared@example.com")
    try:
        with TestClient(second_app) as second:
            for _ in range(2):
                assert client.post(
                    LOGIN_URL,
                    json={"email": "shared@example.com", "password": "Wr0ng-Passw0rd!"},
                ).status_code == 401
            assert second.post(
                LOGIN_URL,
                json={"email": "shared@example.com", "password": "Wr0ng-Passw0rd!"},
            ).status_code == 401

            blocked = second.post(
                LOGIN_URL,
                json={"email": "shared@example.com", "password": STRONG_PASSWORD},
            )
            assert blocked.status_code == 429
            assert blocked.json()["error"]["code"] == "rate_limited"
    finally:
        second_app.state.redis_manager.close()
        second_app.state.database.dispose()


def test_rate_limit_counter_is_atomic(app) -> None:
    limiter = RedisSlidingWindowRateLimiter(
        app.state.redis_manager,
        scope="atomic",
        max_requests=5,
        window_seconds=60,
    )
    limiter.reset("same-key")

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: limiter.check("same-key"), range(20)))

    assert sum(1 for allowed, _ in results if allowed) == 5
    assert sum(1 for allowed, _ in results if not allowed) == 15


def test_rate_limit_keys_do_not_contain_raw_identifiers(client, api, app) -> None:
    api.register("alice@example.com")
    client.post(
        LOGIN_URL,
        json={"email": "alice@example.com", "password": "Wr0ng-Passw0rd!"},
    )

    prefix = f"{app.state.settings.redis_key_prefix}:rate_limit:*"
    keys = list(app.state.redis_manager.client.scan_iter(match=prefix))
    assert keys
    assert all(
        "alice" not in key
        and "example.com" not in key
        and "127.0.0.1" not in key
        for key in keys
    )


def test_redis_rate_limit_window_expires(app) -> None:
    limiter = RedisSlidingWindowRateLimiter(
        app.state.redis_manager,
        scope="expiry",
        max_requests=1,
        window_seconds=1,
    )
    limiter.reset("key")

    assert limiter.check("key")[0] is True
    assert limiter.check("key")[0] is False
    time.sleep(1.1)
    assert limiter.check("key")[0] is True


def test_rate_limit_failure_policies() -> None:
    manager = _FailingManager()
    closed = RedisSlidingWindowRateLimiter(
        manager, scope="closed", max_requests=1, window_seconds=10, failure_policy="closed"
    )
    with pytest.raises(RateLimitUnavailable):
        closed.check("k")

    opened = RedisSlidingWindowRateLimiter(
        manager, scope="open", max_requests=1, window_seconds=10, failure_policy="open"
    )
    assert opened.check("k") == (True, 0.0)


def test_rate_limit_backend_failure_returns_503(client, api, monkeypatch) -> None:
    api.register("alice@example.com")
    headers = api.headers("alice@example.com")
    limiter = client.app.state.rate_limiters["search"]

    def unavailable(_key: str):
        raise RateLimitUnavailable("search")

    monkeypatch.setattr(limiter, "check", unavailable)
    response = client.get("/api/v1/users/search", params={"q": "al"}, headers=headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


def test_readiness_reports_postgres_and_redis(client) -> None:
    response = client.get(READY_URL)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_readiness_returns_503_when_redis_is_down(client, monkeypatch) -> None:
    monkeypatch.setattr(client.app.state.cache, "ping", lambda: False)

    response = client.get(READY_URL)

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["redis"] == "error"


def test_readiness_returns_503_when_database_is_down(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.endpoints.health._database_is_ready", lambda _database: False
    )

    response = client.get(READY_URL)

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["database"] == "error"
