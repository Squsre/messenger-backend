"""Reusable FastAPI dependencies for authentication and authorisation.

The chain is:

``get_bearer_token`` → ``get_token_claims`` → ``get_current_user`` → ``get_current_active_user``

Endpoints depend on the last one (and get a fully validated, active
:class:`~app.db.models.User`), while ``get_token_claims`` is used by logout,
which needs the raw ``jti`` rather than a user.
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
from app.services.token import is_token_revoked
from app.services.user import get_user_by_id, normalize_email

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
) -> User:
    """Resolve the authenticated user from the access token."""
    if is_token_revoked(db, claims.jti):
        raise UnauthorizedError("Token has been revoked.")

    try:
        user_id = int(claims.subject)
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError("Could not validate credentials.") from exc

    user = get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Could not validate credentials.")
    return user


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    """Require an authenticated *and* enabled user."""
    if not user.is_active:
        raise ForbiddenError("This account is disabled.")
    return user


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

    client_host = request.client.host if request.client else "unknown"
    keys = (f"ip:{client_host}", f"email:{normalize_email(email)}")

    retry_after = 0.0
    for key in keys:
        allowed, wait = limiter.check(key)
        if not allowed:
            retry_after = max(retry_after, wait)

    if retry_after > 0:
        raise RateLimitError(retry_after=math.ceil(retry_after))
