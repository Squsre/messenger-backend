"""Turn an access token into an authenticated user.

The HTTP dependency chain and the WebSocket handshake must apply **exactly** the
same rules, so the logic lives here instead of inside ``app.api.deps``. A token
is accepted only when all of the following hold:

1. it verifies (signature, algorithm, audience, issuer, expiry, type),
2. its ``ver`` claim matches ``User.token_version`` — this is how a password
   change, a password reset or "log out everywhere" kills every older token,
3. its ``jti`` is not in the server-side denylist (single-session revocation),
4. if a device session row exists for the ``jti``, that session is not revoked.

Step 4 tolerates a missing row on purpose: access tokens issued before device
sessions existed keep working instead of being invalidated by the upgrade.

Callers that need an *active* account (every HTTP route, and the WebSocket
handshake) check :attr:`User.is_active` themselves, exactly like
``get_current_active_user`` does.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import AccessTokenClaims, TokenError, decode_access_token
from app.db.models import User
from app.services.profile import touch_last_seen
from app.services.session import get_session as get_device_session
from app.services.session import touch_session
from app.services.token import is_token_revoked
from app.services.user import get_user_by_id


def resolve_user_from_claims(
    db: Session,
    claims: AccessTokenClaims,
    settings: Settings,
    *,
    touch_presence: bool = True,
) -> User:
    """Apply checks 2-4 above and return the token's user.

    ``touch_presence`` refreshes ``last_seen_at`` and the device session's
    ``last_active_at`` (both internally throttled). It can be disabled by
    callers that validate a token more frequently than the heartbeat interval.
    """
    user_id = claims.subject_id
    if user_id is None:
        raise UnauthorizedError("Could not validate credentials.")

    user = get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Could not validate credentials.")

    if claims.version != user.token_version:
        raise UnauthorizedError("Could not validate credentials.")

    if is_token_revoked(db, claims.jti):
        raise UnauthorizedError("Token has been revoked.")

    device_session = get_device_session(db, session_id=claims.jti)
    if device_session is not None:
        if device_session.is_revoked:
            raise UnauthorizedError("Token has been revoked.")
        if touch_presence:
            touch_session(
                db,
                device_session,
                interval_seconds=settings.last_seen_update_interval_seconds,
            )

    if touch_presence:
        touch_last_seen(
            db, user, interval_seconds=settings.last_seen_update_interval_seconds
        )

    return user


def resolve_access_token(
    db: Session,
    token: str,
    settings: Settings,
    *,
    touch_presence: bool = True,
) -> tuple[User, AccessTokenClaims]:
    """Decode ``token`` and resolve its user.

    Raises :class:`~app.core.errors.UnauthorizedError` for anything invalid, so
    both HTTP and WebSocket callers produce the same message and code.
    """
    try:
        claims = decode_access_token(token, settings=settings)
    except TokenError as exc:
        raise UnauthorizedError("Could not validate credentials.") from exc

    user = resolve_user_from_claims(
        db, claims, settings, touch_presence=touch_presence
    )
    return user, claims
