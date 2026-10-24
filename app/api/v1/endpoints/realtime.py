"""Realtime chat over WebSocket.

Endpoint: ``/api/v1/ws`` (one socket per client; multiple sockets per account
are allowed up to a configurable cap).

Authentication
--------------
The token is **never** put in the URL or in a query string. After the socket is
accepted, the client must send::

    {"type": "auth", "token": "<access token>"}

as the first frame within ``WS_AUTH_TIMEOUT_SECONDS``. The token is resolved by
:func:`app.services.auth.resolve_access_token`, i.e. exactly the same four
checks the HTTP API applies (signature/expiry, ``token_version``, denylist,
device session), and the account must be active. Nothing is delivered before
authentication succeeds.

Authorization
-------------
There is no "subscribe" frame, so a client cannot ask for an arbitrary
conversation. Sending requires membership (``require_membership`` answers 404),
and an event is only delivered to connections whose accounts are members of
that conversation according to the database at send time. A non-member
therefore can neither read nor write another conversation.

Persistence
-----------
Messages go through :func:`app.services.chat.send_message`, the same function the
HTTP endpoint uses, so validation, block rules, timestamps and the
``last_message_at`` update are shared. A message is committed to the database
*before* it is acknowledged or fanned out; if the recipient is offline it simply
stays in the history.

Statuses and activity
---------------------
* ``delivered`` is emitted to the sender when the message was pushed to a live
  socket of the recipient (``messages.delivered_at`` is stored).
* ``read`` frames advance the caller's read watermark
  (:func:`app.services.chat.mark_read`); a ``read`` event is fanned out to both
  participants so tick marks update without polling.
* ``typing`` is **ephemeral**: it is authorized and forwarded to the other
  participant's live sockets, never written to the database. It carries an
  expiry and is suppressed across a block.
* Closing the last socket of an account records ``last_seen_at``. Whether other
  accounts may read it is controlled by the existing ``show_last_seen`` privacy
  setting; presence is never broadcast on its own.

Errors and limits
-----------------
* Invalid frames produce an ``error`` event and leave the socket open (except an
  invalid token, which closes with 4401).
* Messages are rate limited per account; exceeding the budget returns
  ``rate_limited`` and keeps the connection.
* ``typing`` and ``read`` frames are throttled per connection; the reply is a
  ``rate_limited`` error with ``retry_after_seconds``. A typing ``stop`` is never
  throttled.
* Raw frames larger than ``WS_MAX_FRAME_BYTES`` are rejected before parsing.
* The server sends ``ping`` frames; a client that stays silent for
  ``WS_IDLE_TIMEOUT_SECONDS`` is disconnected and cleaned up.
* The token and account state are re-validated on every chat message **and** on
  a fixed interval (``WS_TOKEN_RECHECK_INTERVAL_SECONDS``) by the same heartbeat
  task, so an idle or ping-only socket cannot outlive a revoked/expired token or
  a disabled account. Revocation is bounded by that interval, not instantaneous.
* Sockets that have not authenticated yet hold a *pending* slot, capped both
  globally and per peer address. The slot is released on success, failure,
  timeout and disconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.config import Settings
from app.core.errors import AppError, ForbiddenError, UnauthorizedError
from app.db.models import User
from app.db.session import Database
from app.schemas.chat import build_message
from app.schemas.realtime import (
    AckEvent,
    AuthFrame,
    DeliveredEvent,
    ErrorEvent,
    FrameError,
    InboundFrame,
    MessageEvent,
    PingEvent,
    PingFrame,
    PongEvent,
    PongFrame,
    ReadEvent,
    ReadFrame,
    ReadyEvent,
    TypingEvent,
    TypingFrame,
    parse_frame,
)
from app.schemas.user import build_public_profile
from app.services import chat as chat_service
from app.services.auth import resolve_access_token
from app.services.contact import is_blocked_between
from app.services.profile import touch_last_seen, visible_last_seen
from app.services.realtime import Connection, ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

#: WebSocket close codes used by this endpoint (the 4xxx range is application
#: specific; 1001/1011 are standard).
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_TOO_MANY_CONNECTIONS = 4429
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_GOING_AWAY = 1001
CLOSE_INTERNAL_ERROR = 1011


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _HandshakeFailure(Exception):
    """Abort the handshake with a specific error and close code."""

    def __init__(
        self, code: str, message: str, *, close_code: int, reason: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.close_code = close_code
        self.reason = reason or message


async def _safe_close(websocket: WebSocket, code: int, reason: str) -> None:
    """Close without letting a transport error escape."""
    try:
        if websocket.application_state is not WebSocketState.DISCONNECTED:
            await websocket.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 - the peer may already be gone
        pass


def _client_key(websocket: WebSocket) -> str:
    """Peer identity used only to cap unauthenticated sockets.

    Behind a reverse proxy this is the proxy address unless the proxy is
    configured to preserve the real client; see the README limitation.
    """
    client = websocket.client
    return client.host if client is not None else "unknown"


async def _send_error(
    manager: ConnectionManager,
    connection: Connection,
    code: str,
    message: str,
    **extra: object,
) -> None:
    await manager.send(
        connection,
        ErrorEvent(code=code, message=message, **extra).model_dump(mode="json"),
    )


async def _receive_first_frame(
    websocket: WebSocket, settings: Settings
) -> AuthFrame:
    """Wait for the auth frame, or raise :class:`_HandshakeFailure`."""
    timeout = settings.ws_auth_timeout_seconds
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=timeout)
    except TimeoutError as exc:
        raise _HandshakeFailure(
            "auth_timeout",
            "The first frame must be an auth frame.",
            close_code=CLOSE_UNAUTHORIZED,
            reason="Authentication timed out",
        ) from exc

    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(code=message.get("code", 1000))

    text = message.get("text")
    if text is None:
        raise _HandshakeFailure(
            "auth_required",
            "The first frame must be a text auth frame.",
            close_code=CLOSE_UNAUTHORIZED,
        )

    try:
        frame = parse_frame(text)
    except FrameError as exc:
        raise _HandshakeFailure(
            exc.code, exc.message, close_code=CLOSE_UNAUTHORIZED
        ) from exc

    if not isinstance(frame, AuthFrame):
        raise _HandshakeFailure(
            "auth_required",
            "The first frame must be an auth frame.",
            close_code=CLOSE_UNAUTHORIZED,
        )
    return frame


async def _authenticate(
    websocket: WebSocket,
    settings: Settings,
    manager: ConnectionManager,
    database: Database,
) -> Connection:
    """Complete the handshake and return a registered connection."""
    frame = await _receive_first_frame(websocket, settings)

    try:
        with database.session() as db:
            user, _claims = resolve_access_token(db, frame.token, settings)
            if not user.is_active:
                raise ForbiddenError("This account is disabled.")
            user_id = user.id
            username = user.username
            profile = build_public_profile(
                user, last_seen=visible_last_seen(viewer=user, target=user)
            )
            conversations = chat_service.conversation_ids_for(db, user_id=user_id)
    except UnauthorizedError as exc:
        raise _HandshakeFailure(
            exc.code,
            exc.message,
            close_code=CLOSE_UNAUTHORIZED,
            reason="Authentication failed",
        ) from exc
    except ForbiddenError as exc:
        raise _HandshakeFailure(
            exc.code,
            exc.message,
            close_code=CLOSE_FORBIDDEN,
            reason="Account is disabled",
        ) from exc

    connection = await manager.register(
        websocket, user_id=user_id, username=username, token=frame.token
    )
    if connection is None:
        raise _HandshakeFailure(
            "too_many_connections",
            "Too many simultaneous connections for this account.",
            close_code=CLOSE_TOO_MANY_CONNECTIONS,
        )

    ready = ReadyEvent(
        connection_id=connection.id,
        user=profile,
        conversations=conversations,
        server_time=_now(),
    )
    if not await manager.send(connection, ready.model_dump(mode="json")):
        raise WebSocketDisconnect(code=1001)

    logger.info(
        "Realtime connection %s opened for user %s", connection.id, connection.user_id
    )
    return connection


def _check_rate_limit(websocket: WebSocket, settings: Settings, user_id: int) -> int:
    """Return the retry-after seconds, or 0 when the message is allowed."""
    if not settings.rate_limit_enabled:
        return 0

    registry = getattr(websocket.app.state, "rate_limiters", None) or {}
    limiter = registry.get("ws")
    if limiter is None:
        return 0

    allowed, wait = limiter.check(f"ws:user:{user_id}")
    if allowed:
        return 0
    return max(1, math.ceil(wait))


async def _dispatch(
    connection: Connection,
    frame: InboundFrame,
    *,
    websocket: WebSocket,
    settings: Settings,
    manager: ConnectionManager,
    database: Database,
) -> None:
    """Handle one authenticated frame."""
    if isinstance(frame, PongFrame):
        # ``_serve`` already refreshed ``last_seen``; nothing else to do.
        return

    if isinstance(frame, PingFrame):
        await manager.send(
            connection, PongEvent(server_time=_now()).model_dump(mode="json")
        )
        return

    if isinstance(frame, AuthFrame):
        await _send_error(
            manager,
            connection,
            "already_authenticated",
            "This connection is already authenticated.",
        )
        return

    if isinstance(frame, TypingFrame):
        await _handle_typing(
            connection,
            frame,
            settings=settings,
            manager=manager,
            database=database,
        )
        return

    if isinstance(frame, ReadFrame):
        await _handle_read(
            connection,
            frame,
            settings=settings,
            manager=manager,
            database=database,
        )
        return

    retry_after = _check_rate_limit(websocket, settings, connection.user_id)
    if retry_after:
        await _send_error(
            manager,
            connection,
            "rate_limited",
            "Too many messages. Please slow down.",
            client_message_id=frame.client_message_id,
            retry_after_seconds=retry_after,
        )
        return

    try:
        with database.session() as db:
            # Re-validate: a token can expire or be revoked while the socket is
            # open, and an account can be disabled. Cheap and consistent with
            # the HTTP rules; the presence heartbeat is throttled internally.
            user, _claims = resolve_access_token(db, connection.token, settings)
            if not user.is_active:
                raise ForbiddenError("This account is disabled.")

            conversation = chat_service.require_membership(
                db, conversation_id=frame.conversation_id, user_id=user.id
            )
            message, created = chat_service.send_message(
                db,
                conversation=conversation,
                sender=user,
                body=frame.body,
                client_message_id=frame.client_message_id,
            )
            member_ids = chat_service.conversation_member_ids(
                db, conversation_id=conversation.id
            )
            # Delivery means "pushed to a live device". Mark it in the same
            # transaction as the message so the ack and the event agree.
            delivered_at = None
            if created:
                recipient_ids = [uid for uid in member_ids if uid != user.id]
                if any(manager.connection_count(uid) for uid in recipient_ids):
                    message.delivered_at = chat_service.utcnow()
                    db.flush()
                    delivered_at = message.delivered_at
            rendered = build_message(message, sender_username=user.username)
    except UnauthorizedError as exc:
        await _send_error(
            manager,
            connection,
            exc.code,
            exc.message,
            client_message_id=frame.client_message_id,
        )
        await manager.close(
            connection,
            code=CLOSE_UNAUTHORIZED,
            reason="Token is no longer valid",
        )
        return
    except ForbiddenError as exc:
        if exc.message == "This account is disabled.":
            await _send_error(
                manager,
                connection,
                exc.code,
                exc.message,
                client_message_id=frame.client_message_id,
            )
            await manager.close(
                connection, code=CLOSE_FORBIDDEN, reason="Account is disabled"
            )
            return
        # A block is reversible: report it and keep the socket usable.
        await _send_error(
            manager,
            connection,
            exc.code,
            exc.message,
            client_message_id=frame.client_message_id,
        )
        return
    except AppError as exc:
        await _send_error(
            manager,
            connection,
            exc.code,
            exc.message,
            client_message_id=frame.client_message_id,
        )
        return

    # The transaction committed when the context manager exited: the message is
    # durable before anybody is told about it.
    await manager.send(
        connection,
        AckEvent(
            client_message_id=frame.client_message_id,
            duplicate=not created,
            message=rendered,
        ).model_dump(mode="json"),
    )
    if created:
        await manager.broadcast(
            user_ids=member_ids,
            payload=MessageEvent(message=rendered).model_dump(mode="json"),
            exclude=connection,
        )
        if delivered_at is not None:
            # The sender's other devices (and only them: this socket's ack
            # already carries the status) learn that the message landed.
            await manager.broadcast(
                user_ids=[connection.user_id],
                payload=DeliveredEvent(
                    conversation_id=frame.conversation_id,
                    message_id=rendered.id,
                    delivered_at=delivered_at,
                ).model_dump(mode="json"),
                exclude=connection,
            )


async def _handle_read(
    connection: Connection,
    frame: ReadFrame,
    *,
    settings: Settings,
    manager: ConnectionManager,
    database: Database,
) -> None:
    """Persist a read watermark and tell both participants' sockets."""
    now = time.monotonic()
    if now - connection.last_read_at < settings.ws_read_min_interval_seconds:
        retry_after = max(
            1,
            math.ceil(
                settings.ws_read_min_interval_seconds
                - (now - connection.last_read_at)
            ),
        )
        await _send_error(
            manager,
            connection,
            "rate_limited",
            "Read receipts are being sent too quickly.",
            retry_after_seconds=retry_after,
        )
        return
    connection.last_read_at = now

    try:
        with database.session() as db:
            user, _claims = resolve_access_token(db, connection.token, settings)
            if not user.is_active:
                raise ForbiddenError("This account is disabled.")

            conversation = chat_service.require_membership(
                db, conversation_id=frame.conversation_id, user_id=user.id
            )
            state = chat_service.mark_read(
                db,
                conversation_id=conversation.id,
                reader_id=user.id,
                last_message_id=frame.last_message_id,
            )
            member_ids = chat_service.conversation_member_ids(
                db, conversation_id=conversation.id
            )
            reader_username = user.username
    except UnauthorizedError as exc:
        await _send_error(manager, connection, exc.code, exc.message)
        await manager.close(
            connection, code=CLOSE_UNAUTHORIZED, reason="Token is no longer valid"
        )
        return
    except ForbiddenError as exc:
        if exc.message == "This account is disabled.":
            await _send_error(manager, connection, exc.code, exc.message)
            await manager.close(
                connection, code=CLOSE_FORBIDDEN, reason="Account is disabled"
            )
            return
        await _send_error(manager, connection, exc.code, exc.message)
        return
    except AppError as exc:
        await _send_error(manager, connection, exc.code, exc.message)
        return

    if state.updated and state.read_at is not None:
        await manager.broadcast(
            user_ids=member_ids,
            payload=ReadEvent(
                conversation_id=conversation.id,
                message_id=state.last_read_message_id,
                reader_username=reader_username,
                read_at=state.read_at,
            ).model_dump(mode="json"),
        )


async def _handle_typing(
    connection: Connection,
    frame: TypingFrame,
    *,
    settings: Settings,
    manager: ConnectionManager,
    database: Database,
) -> None:
    """Forward an ephemeral typing state to the other participant.

    Nothing is written to the database: the indicator carries an expiry so a
    client that disappears cannot leave it stuck on. Only the ``start`` frames
    are throttled; a ``stop`` is always forwarded.
    """
    if frame.is_typing:
        now = time.monotonic()
        if now - connection.last_typing_at < settings.ws_typing_min_interval_seconds:
            retry_after = max(
                1,
                math.ceil(
                    settings.ws_typing_min_interval_seconds
                    - (now - connection.last_typing_at)
                ),
            )
            await _send_error(
                manager,
                connection,
                "rate_limited",
                "Typing updates are being sent too quickly.",
                retry_after_seconds=retry_after,
            )
            return
        connection.last_typing_at = now

    try:
        with database.session() as db:
            user, _claims = resolve_access_token(db, connection.token, settings)
            if not user.is_active:
                raise ForbiddenError("This account is disabled.")

            conversation = chat_service.require_membership(
                db, conversation_id=frame.conversation_id, user_id=user.id
            )
            recipient = chat_service.other_participant(
                db, conversation_id=conversation.id, viewer_id=user.id
            )
            if is_blocked_between(db, user.id, recipient.id):
                # Typing is a send: it is suppressed in both directions.
                raise ForbiddenError("This user is not available.")
            recipient_id = recipient.id
            username = user.username
    except UnauthorizedError as exc:
        await _send_error(manager, connection, exc.code, exc.message)
        await manager.close(
            connection, code=CLOSE_UNAUTHORIZED, reason="Token is no longer valid"
        )
        return
    except ForbiddenError as exc:
        if exc.message == "This account is disabled.":
            await _send_error(manager, connection, exc.code, exc.message)
            await manager.close(
                connection, code=CLOSE_FORBIDDEN, reason="Account is disabled"
            )
            return
        await _send_error(manager, connection, exc.code, exc.message)
        return
    except AppError as exc:
        await _send_error(manager, connection, exc.code, exc.message)
        return

    await manager.broadcast(
        user_ids=[recipient_id],
        payload=TypingEvent(
            conversation_id=conversation.id,
            username=username,
            is_typing=frame.is_typing,
            expires_in_seconds=settings.ws_typing_expiry_seconds,
        ).model_dump(mode="json"),
    )


async def _revalidate(
    connection: Connection,
    settings: Settings,
    manager: ConnectionManager,
    database: Database,
) -> bool:
    """Re-check the connection's token and account state.

    Runs from the heartbeat task on a fixed interval, independently of the
    frames the client sends, so an idle or ping-only socket cannot outlive its
    token. Returns ``True`` while the connection may stay open.
    """
    try:
        with database.session() as db:
            user, _claims = resolve_access_token(db, connection.token, settings)
            if not user.is_active:
                raise ForbiddenError("This account is disabled.")
    except UnauthorizedError as exc:
        await _send_error(manager, connection, exc.code, exc.message)
        await manager.close(
            connection, code=CLOSE_UNAUTHORIZED, reason="Token is no longer valid"
        )
        return False
    except ForbiddenError as exc:
        await _send_error(manager, connection, exc.code, exc.message)
        await manager.close(
            connection, code=CLOSE_FORBIDDEN, reason="Account is disabled"
        )
        return False
    except Exception:  # noqa: BLE001 - never keep an unvalidated socket open
        logger.exception(
            "Token revalidation failed for realtime connection %s", connection.id
        )
        await manager.close(
            connection, code=CLOSE_INTERNAL_ERROR, reason="Validation failed"
        )
        return False
    return True


async def _heartbeat(
    connection: Connection,
    settings: Settings,
    manager: ConnectionManager,
    database: Database,
) -> None:
    """Ping idle clients and re-validate the token on a fixed interval.

    A single task does both jobs (no extra task per connection). It is
    cancelled by ``_serve`` in every exit path.
    """
    now = time.monotonic()
    next_ping = now + settings.ws_heartbeat_interval_seconds
    next_check = now + settings.ws_token_recheck_interval_seconds

    while True:
        delay = min(next_ping, next_check) - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

        now = time.monotonic()
        if now >= next_check:
            next_check = now + settings.ws_token_recheck_interval_seconds
            if not await _revalidate(connection, settings, manager, database):
                return

        now = time.monotonic()
        if now >= next_ping:
            next_ping = now + settings.ws_heartbeat_interval_seconds
            if connection.idle_seconds() >= settings.ws_idle_timeout_seconds:
                await manager.close(
                    connection, code=CLOSE_GOING_AWAY, reason="Idle timeout"
                )
                return
            if not await manager.send(
                connection, PingEvent(server_time=_now()).model_dump(mode="json")
            ):
                return


async def _serve(
    connection: Connection,
    *,
    websocket: WebSocket,
    settings: Settings,
    manager: ConnectionManager,
    database: Database,
) -> None:
    heartbeat = asyncio.create_task(_heartbeat(connection, settings, manager, database))
    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                return

            connection.touch()

            text = message.get("text")
            if text is None:
                await _send_error(
                    manager,
                    connection,
                    "unsupported_frame",
                    "Only text frames are supported.",
                )
                continue

            if len(text.encode("utf-8")) > settings.ws_max_frame_bytes:
                await _send_error(
                    manager,
                    connection,
                    "frame_too_large",
                    "Frame exceeds the maximum allowed size.",
                )
                continue

            try:
                frame = parse_frame(text)
            except FrameError as exc:
                await _send_error(manager, connection, exc.code, exc.message)
                continue

            await _dispatch(
                connection,
                frame,
                websocket=websocket,
                settings=settings,
                manager=manager,
                database=database,
            )
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001 - never let one socket take the app down
        logger.exception(
            "Realtime connection %s for user %s failed",
            connection.id,
            connection.user_id,
        )
        await _safe_close(websocket, CLOSE_INTERNAL_ERROR, "Internal error")
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await manager.unregister(connection)
        # Last-seen is only recorded when the account has no other live socket;
        # closing one device must not make the user look offline on another.
        # ``show_last_seen`` decides whether other accounts may read the value
        # (see ``visible_last_seen``), so nothing needs to be broadcast here.
        try:
            with database.session() as db:
                if manager.connection_count(connection.user_id) == 0:
                    user = db.get(User, connection.user_id)
                    if user is not None:
                        touch_last_seen(db, user, interval_seconds=0)
        except Exception:  # noqa: BLE001 - cleanup must never mask the close
            logger.exception(
                "Could not record last seen for user %s", connection.user_id
            )
        await _safe_close(websocket, CLOSE_GOING_AWAY, "Connection closed")
        logger.info(
            "Realtime connection %s closed for user %s",
            connection.id,
            connection.user_id,
        )


@router.websocket("/ws")
async def realtime_socket(websocket: WebSocket) -> None:
    """Authenticated realtime chat socket.

    This operation is intentionally **not** part of the OpenAPI document
    (OpenAPI 3.0 cannot describe WebSocket frames); the protocol is documented
    in ``README.md`` under "Realtime chat (WebSocket)".
    """
    settings: Settings = websocket.app.state.settings
    manager: ConnectionManager = websocket.app.state.ws_manager
    database: Database = websocket.app.state.database

    await websocket.accept()

    # Cap sockets that never authenticate *before* doing any handshake work.
    # The reservation is released in the ``finally`` below, so success, invalid
    # tokens, timeouts, client disconnects and shutdown all free the slot.
    ticket = await manager.reserve_pending(_client_key(websocket))
    if ticket is None:
        logger.info("Rejecting a realtime connection: pending limit reached")
        await _safe_send_handshake_error(
            websocket,
            _HandshakeFailure(
                "server_busy",
                "The server is accepting too many connections right now.",
                close_code=CLOSE_TRY_AGAIN_LATER,
            ),
        )
        return

    try:
        connection = await _authenticate(websocket, settings, manager, database)
    except _HandshakeFailure as failure:
        await _safe_send_handshake_error(websocket, failure)
        return
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001 - handshake must never crash the app
        logger.exception("Realtime handshake failed")
        await _safe_close(websocket, CLOSE_INTERNAL_ERROR, "Internal error")
        return
    finally:
        await manager.release_pending(ticket)

    await _serve(
        connection,
        websocket=websocket,
        settings=settings,
        manager=manager,
        database=database,
    )


async def _safe_send_handshake_error(
    websocket: WebSocket, failure: _HandshakeFailure
) -> None:
    try:
        await websocket.send_json(
            ErrorEvent(code=failure.code, message=failure.message).model_dump(
                mode="json"
            )
        )
    except Exception:  # noqa: BLE001 - the peer may be gone
        pass
    await _safe_close(websocket, failure.close_code, failure.reason)
