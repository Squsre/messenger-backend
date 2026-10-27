"""In-process registry of live WebSocket connections.

Scope and limits
----------------
This registry lives in **one process**. It is enough for a single uvicorn
worker and for the current deployment, but it does not coordinate across
workers or hosts: with more than one process, a message sent to worker A is
only fanned out to sockets held by worker A. Redis is available for cache and
shared rate limiting, but a shared WebSocket pub/sub layer is deliberately still
a follow-up and is not part of this phase.

Invariants
----------
* A user may hold several connections (one per device/tab). Exceeding the
  configured cap rejects the **new** connection with a policy close code and
  never evicts a working one.
* Sockets that have not authenticated yet hold a short-lived *pending* slot,
  limited globally and per peer address. The slot is released on success,
  failure, timeout and disconnect, so a burst of silent sockets cannot pin
  resources.
* A dead socket is removed lazily: a failed send marks the connection closed
  and unregisters it, and the endpoint's ``finally`` block unregisters again
  (idempotent).
* Sending to one connection cannot raise into another: every send is guarded
  individually, so one broken device does not interrupt the rest.
* The audience is always supplied by the caller from the **database** (the
  conversation's member ids). The registry never lets a connection widen its own
  audience.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(eq=False)
class Connection:
    """One live socket belonging to one account."""

    websocket: WebSocket
    user_id: int
    username: str
    #: Kept in memory only, for re-validation on every inbound message. It is
    #: excluded from ``repr`` and is never logged, serialised or echoed.
    token: str = field(repr=False)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    connected_at: datetime = field(default_factory=utcnow)
    last_seen: float = field(default_factory=time.monotonic)
    #: Monotonic timestamps of the last typing/read frame, used for per-socket
    #: throttling. They are not presence information and are never serialised.
    last_typing_at: float = 0.0
    last_read_at: float = 0.0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    def touch(self) -> None:
        """Record inbound activity (any frame, including ``pong``)."""
        self.last_seen = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_seen


@dataclass
class PendingConnection:
    """A reserved slot for a socket that has not authenticated yet.

    Held from ``accept`` until the handshake succeeds, fails or the client
    disconnects. Releasing is idempotent so every exit path can call it.
    """

    key: str
    released: bool = False


class ConnectionManager:
    """Tracks live connections per user and fans events out to them."""

    def __init__(
        self,
        *,
        max_per_user: int,
        max_pending_connections: int,
        max_pending_per_key: int,
    ) -> None:
        self.max_per_user = max_per_user
        #: Sockets that are connected but have not authenticated yet. They are
        #: counted globally and per client key (the peer address) so a burst of
        #: silent, unauthenticated sockets cannot exhaust server resources.
        self.max_pending_connections = max_pending_connections
        self.max_pending_per_key = max_pending_per_key
        self._connections: dict[int, set[Connection]] = {}
        self._pending_total = 0
        self._pending_by_key: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def reserve_pending(self, key: str) -> PendingConnection | None:
        """Reserve a pre-auth slot, or ``None`` when a limit is reached."""
        async with self._lock:
            if self._pending_total >= self.max_pending_connections:
                return None
            if self._pending_by_key.get(key, 0) >= self.max_pending_per_key:
                return None
            self._pending_total += 1
            self._pending_by_key[key] = self._pending_by_key.get(key, 0) + 1
            return PendingConnection(key=key)

    async def release_pending(self, ticket: PendingConnection) -> None:
        """Release a reservation; safe to call more than once."""
        async with self._lock:
            if ticket.released:
                return
            ticket.released = True
            self._pending_total = max(0, self._pending_total - 1)
            remaining = self._pending_by_key.get(ticket.key, 0) - 1
            if remaining > 0:
                self._pending_by_key[ticket.key] = remaining
            else:
                self._pending_by_key.pop(ticket.key, None)

    def pending_count(self) -> int:
        """Best-effort number of sockets awaiting authentication."""
        return self._pending_total

    def pending_count_for(self, key: str) -> int:
        return self._pending_by_key.get(key, 0)

    async def register(
        self, websocket: WebSocket, *, user_id: int, username: str, token: str
    ) -> Connection | None:
        """Register a socket, or return ``None`` when the per-user cap is hit."""
        async with self._lock:
            existing = self._connections.setdefault(user_id, set())
            if len(existing) >= self.max_per_user:
                # Reject the new one; never disconnect a working device.
                return None
            connection = Connection(
                websocket=websocket,
                user_id=user_id,
                username=username,
                token=token,
            )
            existing.add(connection)
            return connection

    async def unregister(self, connection: Connection) -> None:
        """Remove a connection; safe to call more than once."""
        async with self._lock:
            connections = self._connections.get(connection.user_id)
            if connections is None:
                return
            connections.discard(connection)
            if not connections:
                self._connections.pop(connection.user_id, None)

    def connection_count(self, user_id: int) -> int:
        """Best-effort count (used by tests and operational checks)."""
        return len(self._connections.get(user_id, ()))

    def connections_for(self, user_id: int) -> tuple[Connection, ...]:
        return tuple(self._connections.get(user_id, ()))

    async def send(self, connection: Connection, payload: dict) -> bool:
        """Send one frame; on failure mark it dead and return ``False``."""
        if connection.closed:
            return False
        async with connection.send_lock:
            try:
                await connection.websocket.send_json(payload)
                return True
            except Exception:  # noqa: BLE001 - one bad socket must not break others
                connection.closed = True
                logger.info(
                    "Dropping realtime connection %s for user %s after a send failure",
                    connection.id,
                    connection.user_id,
                )
        await self.unregister(connection)
        return False

    async def broadcast(
        self,
        *,
        user_ids: Iterable[int],
        payload: dict,
        exclude: Connection | None = None,
    ) -> int:
        """Deliver ``payload`` to every connection of ``user_ids`` (except ``exclude``)."""
        targets = [
            connection
            for user_id in set(user_ids)
            for connection in tuple(self._connections.get(user_id, ()))
            if connection is not exclude
        ]
        delivered = 0
        for connection in targets:
            if await self.send(connection, payload):
                delivered += 1
        return delivered

    async def close(self, connection: Connection, *, code: int, reason: str) -> None:
        """Close and unregister a connection, ignoring transport errors."""
        async with connection.send_lock:
            connection.closed = True
            try:
                await connection.websocket.close(code=code, reason=reason)
            except Exception:  # noqa: BLE001 - the peer may already be gone
                pass
        await self.unregister(connection)

    async def close_all(self, *, code: int, reason: str) -> int:
        """Close every connection (used on application shutdown)."""
        all_connections = [
            connection
            for connections in tuple(self._connections.values())
            for connection in tuple(connections)
        ]
        for connection in all_connections:
            await self.close(connection, code=code, reason=reason)

        # Pending sockets are closed by their endpoint's ``finally`` too, but
        # clearing here guarantees the counters cannot survive a shutdown.
        async with self._lock:
            self._pending_total = 0
            self._pending_by_key.clear()
        return len(all_connections)
