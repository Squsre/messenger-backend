"""A tiny, dependency-free in-process rate limiter.

Rationale
---------
Login/registration are the endpoints worth throttling. A distributed limiter
would need Redis (explicitly out of scope for this phase), so this module
implements a **sliding-window** limiter in memory. It is honest about its
limits: state lives in a single process, so with multiple workers each worker
enforces its own bucket, and it resets on restart. The README documents this
and the migration path to a shared store.

The limiter is deliberately small (no external dependency) and thread-safe
(``threading.Lock``), which is enough for the current single-process ASGI
deployment.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    """Allow at most ``max_requests`` events per ``window_seconds`` and key."""

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
