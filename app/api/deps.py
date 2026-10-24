"""Reusable FastAPI dependencies for authentication, throttling and services.

The authentication chain is:

``get_bearer_token`` → ``get_token_claims`` → ``get_current_user`` → ``get_current_active_user``

Endpoints depend on the last one (and get a fully validated, active
:class:`~app.db.models.User`), while ``get_token_claims`` is used by logout,
which needs the raw ``jti`` rather than a user.

A token is accepted only when **all** of the following hold:

1. it verifies (signature, algorithm, audience, issuer, expiry, type),
2. its ``ver`` claim matches ``User.token_version`` — this is how a password
   change, a password reset or "log out everywhere" kills every older token,
3. its ``jti`` is not in the server-side denylist (single-session revocation), and
4. if a device session row exists for the ``jti``, that session is not revoked.

Step 4 tolerates a missing row on purpose: access tokens issued before device
sessions existed keep working instead of being invalidated by the upgrade.
"""

from __future__ import annotations

import math

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.errors import ForbiddenError, RateLimitError, UnauthorizedError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import AccessTokenClaims, TokenError, decode_access_token
from app.db.models import User
from app.db.session import get_db
from app.services.auth import resolve_user_from_claims
from app.services.email import EmailService, build_email_service
from app.services.storage import StorageService
from app.services.user import normalize_email

# ``auto_error=False`` lets us raise our own 401 with a consistent body and
# ``WWW-Authenticate`` header instead of FastAPI's default 403.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="HTTPBearer",
    description="Access token issued by POST /api/v1/auth/login.",
)


def get_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Extract the raw Bearer token, or fail with 401."""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise UnauthorizedError("Not authenticated.")
    return credentials.credentials


def get_token_claims(
    token: str = Depends(get_bearer_token),
    settings: Settings = Depends(get_app_settings),
) -> AccessTokenClaims:
    """Verify the access token and return its claims."""
    try:
        return decode_access_token(token, settings=settings)
    except TokenError as exc:
        raise UnauthorizedError("Could not validate credentials.") from exc


def get_current_user(
    claims: AccessTokenClaims = Depends(get_token_claims),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Resolve the authenticated user and refresh their activity heartbeat.

    The four validation layers (claims, ``token_version``, denylist, device
    session) live in :func:`app.services.auth.resolve_user_from_claims` so the
    WebSocket handshake cannot drift away from the HTTP rules.
    """
    return resolve_user_from_claims(db, claims, settings)


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    """Require an authenticated *and* enabled user."""
    if not user.is_active:
        raise ForbiddenError("This account is disabled.")
    return user


# --- shared services --------------------------------------------------------


def get_email_service(
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> EmailService:
    """Return the configured email transport.

    The application attaches one to ``app.state`` at startup; the fallback keeps
    ad-hoc app instances (scripts, tests) working.
    """
    service = getattr(request.app.state, "email_service", None)
    return service if service is not None else build_email_service(settings)


def get_storage(request: Request) -> StorageService:
    """Return the storage backend attached to the application at startup."""
    storage = getattr(request.app.state, "storage", None)
    if storage is None:  # pragma: no cover - every app factory sets this
        raise RuntimeError("media storage is not configured")
    return storage


def get_client_ip(request: Request) -> str | None:
    """Best-effort client address.

    ``request.client`` is the transport peer, so behind a proxy this is the
    proxy's address; the README documents how to source the real client IP when
    a trusted reverse proxy is introduced.
    """
    return request.client.host if request.client else None


# --- rate limiting ----------------------------------------------------------


def get_rate_limiter(request: Request, scope: str) -> SlidingWindowRateLimiter | None:
    """Return the limiter registered for ``scope``, if any."""
    registry = getattr(request.app.state, "rate_limiters", None) or {}
    return registry.get(scope)


def enforce_rate_limit(request: Request, settings: Settings, scope: str, *keys: str) -> None:
    """Throttle a request against the limiter registered for ``scope``.

    Every key is checked and the longest wait wins (so a "per account" and a
    "per address" rule can be combined). Raises :class:`RateLimitError`
    (429 + ``Retry-After``) when any key is exhausted.
    """
    if not settings.rate_limit_enabled:
        return

    limiter = get_rate_limiter(request, scope)
    if limiter is None:
        return

    retry_after = 0.0
    for key in keys:
        allowed, wait = limiter.check(f"{scope}:{key}")
        if not allowed:
            retry_after = max(retry_after, wait)

    if retry_after > 0:
        raise RateLimitError(retry_after=math.ceil(retry_after))


def get_login_rate_limiter(request: Request) -> SlidingWindowRateLimiter | None:
    """Return the application's login rate limiter, if one is configured."""
    return getattr(request.app.state, "login_rate_limiter", None)


def enforce_login_rate_limit(request: Request, settings: Settings, email: str) -> None:
    """Throttle login attempts by client IP and by target account.

    Two keys are checked so that neither a single host hammering many accounts
    nor a distributed attempt on one account goes unnoticed. Raises
    :class:`RateLimitError` (429 + ``Retry-After``) once a limit is exceeded.
    """
    if not settings.rate_limit_enabled:
        return

    limiter = get_login_rate_limiter(request)
    if limiter is None:
        return

    client_host = get_client_ip(request) or "unknown"
    keys = (f"ip:{client_host}", f"email:{normalize_email(email)}")

    retry_after = 0.0
    for key in keys:
        allowed, wait = limiter.check(key)
        if not allowed:
            retry_after = max(retry_after, wait)

    if retry_after > 0:
        raise RateLimitError(retry_after=math.ceil(retry_after))
