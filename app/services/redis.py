"""Process-wide Redis connection shared by the cache and rate limiter.

Redis is intentionally *not* a source of truth in this project. It holds a
bounded public-profile cache and the shared rate-limit counters, both of which
can be lost without losing durable data. The client and its connection pool are
created once per application process and closed from the FastAPI lifespan.

The application's request path is synchronous (SQLAlchemy ``Session`` and sync
endpoints), so the thread-safe redis-py client is the right fit: it is safe to
call from the worker threads FastAPI uses for sync endpoints and from the async
WebSocket handler. redis-py also exposes an asyncio client, but mixing event
loops with the existing sync database path would add a bridge without improving
the request flow.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import redis
from redis.exceptions import RedisError

from app.core.config import Settings

logger = logging.getLogger(__name__)


def redact_redis_url(url: str) -> str:
    """Return ``url`` with any password replaced by ``***``.

    Redis URLs can contain a password (``redis://:secret@host:6379/0``).
    Startup logs and diagnostics must never print it verbatim.
    """
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    auth = ""
    if parsed.username:
        auth = f"{parsed.username}:***@"
    elif parsed.password:
        auth = ":***@"
    return f"{parsed.scheme}://{auth}{host}{parsed.path}"


class RedisManager:
    """Own the one Redis client used by a single application process."""

    def __init__(self, settings: Settings) -> None:
        self.url = settings.redis_url
        self.prefix = settings.redis_key_prefix
        self._client = redis.Redis.from_url(
            self.url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            health_check_interval=settings.redis_health_check_interval_seconds,
        )

    @property
    def client(self) -> redis.Redis:
        """The shared redis-py client (thread-safe connection pool)."""
        return self._client

    @property
    def safe_url(self) -> str:
        """The URL with any password redacted, safe for logs."""
        return redact_redis_url(self.url)

    def key(self, *parts: object) -> str:
        """Build a namespaced key, e.g. ``messenger:cache:profile:alice``."""
        suffix = ":".join(str(part) for part in parts)
        return f"{self.prefix}:{suffix}" if suffix else self.prefix

    def ping(self) -> bool:
        """Return whether Redis answers. Never raises."""
        try:
            return bool(self._client.ping())
        except RedisError:
            logger.warning("Redis ping failed (%s)", self.safe_url, exc_info=True)
            return False

    def close(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        try:
            self._client.close()
        except RedisError:  # pragma: no cover - close is best-effort
            logger.warning("Could not close Redis client cleanly", exc_info=True)

    def delete_namespace(self) -> int:
        """Delete every key under this application's prefix.

        Used by tests and by deliberate operational cleanup. It never flushes
        the whole Redis database, so another application sharing the instance is
        not affected.
        """
        deleted = 0
        try:
            for key in self._client.scan_iter(match=f"{self.prefix}:*", count=500):
                deleted += int(self._client.delete(key))
        except RedisError:
            logger.warning("Could not clear Redis namespace", exc_info=True)
        return deleted
