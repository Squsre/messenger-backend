"""Small, fail-open cache layer on top of Redis.

Only disposable, non-sensitive values belong here. The cache never stores
passwords, access tokens, verification tokens, email addresses or private
profile fields. Redis failures are logged and treated as a cache miss so a
cache outage cannot take the API down.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.exceptions import RedisError

from app.services.redis import RedisManager

logger = logging.getLogger(__name__)


class CacheService:
    """Namespaced ``get`` / ``set`` / ``delete`` operations with JSON helpers."""

    def __init__(self, manager: RedisManager, *, enabled: bool = True) -> None:
        self._manager = manager
        self.enabled = enabled

    def key(self, name: str) -> str:
        return self._manager.key("cache", name)

    def get(self, name: str) -> str | None:
        """Return a cached string, or ``None`` on a miss or Redis error."""
        if not self.enabled:
            return None
        try:
            return self._manager.client.get(self.key(name))
        except RedisError:
            logger.warning("Redis cache read failed", exc_info=True)
            return None

    def set(self, name: str, value: str, ttl_seconds: int) -> bool:
        """Store a string with a TTL. Returns ``False`` on a Redis error."""
        if not self.enabled:
            return False
        try:
            self._manager.client.set(self.key(name), value, ex=max(1, int(ttl_seconds)))
            return True
        except RedisError:
            logger.warning("Redis cache write failed", exc_info=True)
            return False

    def delete(self, name: str) -> bool:
        """Delete a key. Missing keys and Redis errors are harmless."""
        if not self.enabled:
            return False
        try:
            return bool(self._manager.client.delete(self.key(name)))
        except RedisError:
            logger.warning("Redis cache delete failed", exc_info=True)
            return False

    def get_json(self, name: str) -> Any | None:
        """Read and decode a JSON value; malformed entries are discarded."""
        raw = self.get(name)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Discarding malformed cached JSON for %s", name)
            self.delete(name)
            return None

    def set_json(self, name: str, value: Any, ttl_seconds: int) -> bool:
        """Encode ``value`` as JSON and store it with a TTL."""
        try:
            encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            logger.warning("Could not JSON-encode cache value for %s", name)
            return False
        return self.set(name, encoded, ttl_seconds=ttl_seconds)

    def ping(self) -> bool:
        return self._manager.ping()
