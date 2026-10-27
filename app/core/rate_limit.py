"""Rate limiting primitives.

The application uses :class:`RedisSlidingWindowRateLimiter`, a shared,
atomic sliding-window limiter backed by Redis. Counters are therefore visible
to every worker and process using the same Redis database, and they survive a
single API process restart (until their TTL expires).

The small in-memory :class:`SlidingWindowRateLimiter` is retained for unit tests
of the window semantics and for ad-hoc tooling. It is **not** wired into the
application: a per-process limiter cannot enforce a shared budget once more than
one worker is running.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from collections import deque

from redis.exceptions import RedisError

from app.services.redis import RedisManager

logger = logging.getLogger(__name__)


class RateLimitUnavailable(RuntimeError):
    """Redis could not answer a rate-limit check while fail-closed is selected."""

    def __init__(self, scope: str) -> None:
        super().__init__(f"rate limiter backend unavailable for scope {scope!r}")
        self.scope = scope


class SlidingWindowRateLimiter:
    """Allow at most ``max_requests`` events per ``window_seconds`` and key.

    This is an in-memory reference implementation. It is thread-safe but
    deliberately not shared between processes; use the Redis limiter in the
    application.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        max_keys: int = 10_000,
        clock=time.monotonic,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self.max_keys = max_keys
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def _evict_empty(self) -> None:
        """Drop buckets that are no longer tracking anything."""
        if len(self._events) <= self.max_keys:
            return
        for key in [k for k, v in self._events.items() if not v]:
            self._events.pop(key, None)

    def check(self, key: str) -> tuple[bool, float]:
        """Record an attempt for ``key``.

        Returns ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is
        ``0.0`` when the attempt is allowed.
        """
        now = self._clock()
        with self._lock:
            events = self._prune(key, now)

            if len(events) >= self.max_requests:
                retry_after = self.window_seconds - (now - events[0])
                return False, max(retry_after, 0.0)

            events.append(now)
            self._evict_empty()
            return True, 0.0

    def reset(self, key: str | None = None) -> None:
        """Clear the state for ``key`` (or everything when omitted)."""
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


class RedisSlidingWindowRateLimiter:
    """A shared sliding-window limiter with an atomic Redis implementation.

    The whole read-prune-count-insert-expire operation runs inside one Lua
    script. That makes concurrent requests from different workers safe: two
    requests cannot both observe ``max_requests - 1`` and overrun the budget.
    Redis's own clock is used so process clock skew cannot move the window.
    """

    # ARGV[1] = window in milliseconds
    # ARGV[2] = maximum requests
    # ARGV[3] = unique member for this attempt
    _SCRIPT = """
local key = KEYS[1]
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]

local now_parts = redis.call('TIME')
local now_ms = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
local cutoff = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)

if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_ms = window_ms - (now_ms - tonumber(oldest[2]))
    if retry_ms < 0 then
        retry_ms = 0
    end
    redis.call('PEXPIRE', key, window_ms + 1000)
    return {0, retry_ms}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms + 1000)
return {1, 0}
"""

    def __init__(
        self,
        manager: RedisManager,
        *,
        scope: str,
        max_requests: int,
        window_seconds: float,
        failure_policy: str = "closed",
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if failure_policy not in {"open", "closed"}:
            raise ValueError("failure_policy must be 'open' or 'closed'")
        self._manager = manager
        self.scope = scope
        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self.failure_policy = failure_policy

    def _key(self, key: str) -> str:
        # Hash the identifier so raw e-mail addresses and IP addresses are not
        # written to Redis. The readable scope still makes keys debuggable.
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._manager.key("rate_limit", self.scope, digest)

    def check(self, key: str) -> tuple[bool, float]:
        """Record an attempt and return ``(allowed, retry_after_seconds)``.

        On a Redis failure the configured policy decides the outcome:

        * ``closed`` raises :class:`RateLimitUnavailable` (the API maps this to
          ``503`` so a Redis outage cannot silently disable login throttling);
        * ``open`` allows the request, trading protection for availability.
        """
        try:
            result = self._manager.client.eval(
                self._SCRIPT,
                1,
                self._key(key),
                int(self.window_seconds * 1000),
                self.max_requests,
                uuid.uuid4().hex,
            )
        except RedisError as exc:
            if self.failure_policy == "open":
                logger.warning(
                    "Rate limiter unavailable for scope %s; failing open",
                    self.scope,
                    exc_info=True,
                )
                return True, 0.0
            logger.error(
                "Rate limiter unavailable for scope %s; failing closed",
                self.scope,
                exc_info=True,
            )
            raise RateLimitUnavailable(self.scope) from exc

        allowed = bool(int(result[0]))
        retry_after = max(float(result[1]) / 1000.0, 0.0)
        return allowed, retry_after

    def reset(self, key: str) -> None:
        """Delete this key's shared counter (useful in tests/administration)."""
        try:
            self._manager.client.delete(self._key(key))
        except RedisError:
            logger.warning("Could not reset rate-limit key", exc_info=True)
