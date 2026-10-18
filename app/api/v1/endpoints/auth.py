"""Authentication endpoints.

Routes (all under ``/api/v1/auth``):

* ``POST /register`` — create an account
* ``POST /login``    — exchange credentials for an access token
* ``GET  /me``       — the authenticated user
* ``POST /logout``   — revoke the presented access token
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.security import AccessTokenClaims, access_token_ttl_seconds, create_access_token
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import LoginRequest, MessageResponse, TokenResponse, UserCreate, UserRead
from app.services import verification as verification_service
from app.services.email import EmailService
from app.services.profile import touch_last_seen
from app.services.session import get_session, record_session, revoke_session
from app.services.token import prune_expired_tokens, revoke_token
from app.services.user import authenticate_user, create_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_CONFLICT_RESPONSE = {
    "description": "An account with this email already exists.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "conflict",
                    "message": "An account with this email already exists.",
                }
            }
        }
    },
}

_VALIDATION_RESPONSE = {
    "description": "The submitted payload is invalid (for example a weak password).",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": [{"type": "value_error", "loc": ["body", "password"], "msg": "..."}],
                }
            }
        }
    },
}

_UNAUTHORIZED_RESPONSE = {
    "description": "Missing, invalid, expired or revoked credentials.",
    "content": {
        "application/json": {
            "example": {
                "error": {"code": "unauthorized", "message": "Could not validate credentials."}
            }
        }
    },
}

_FORBIDDEN_RESPONSE = {
    "description": "Credentials were valid but the account is disabled.",
    "content": {
        "application/json": {
            "example": {"error": {"code": "forbidden", "message": "This account is disabled."}}
        }
    },
}

_RATE_LIMIT_RESPONSE = {
    "description": "Too many login attempts. Retry after the ``Retry-After`` interval.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please try again later.",
                }
            }
        }
    },
}


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
    description=(
        "Create an account with an email and password. The email is normalised "
        "to lower case and must be unique. The password is stored only as an "
        "Argon2id hash."
    ),
    responses={409: _CONFLICT_RESPONSE, 422: _VALIDATION_RESPONSE},
)
def register(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    email_service: EmailService = Depends(deps.get_email_service),
) -> User:
    """Create an account and return its public representation.

    A verification email is sent opportunistically. Delivery problems are logged
    and swallowed: registration must not fail because the mail transport is
    unavailable, and the link can always be requested again.
    """
    user = create_user(db, email=payload.email, password=payload.password)

    try:
        # The savepoint keeps a delivery failure from discarding the new account.
        with db.begin_nested():
            verification_service.issue_email_verification(
                db,
                user=user,
                settings=settings,
                email_service=email_service,
            )
    except Exception:  # pragma: no cover - depends on the transport
        logger.exception(
            "Could not send the initial verification email for user id=%s", user.id
        )

    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in",
    description=(
        "Exchange email and password for a short-lived access token. The same "
        "error is returned for an unknown email and a wrong password."
    ),
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _FORBIDDEN_RESPONSE,
        422: _VALIDATION_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Authenticate and issue an access token."""
    deps.enforce_login_rate_limit(request, settings, payload.email)

    user = authenticate_user(db, email=payload.email, password=payload.password)

    # Opportunistic cleanup so the denylist does not grow forever.
    prune_expired_tokens(db)

    # The session id *is* the token id, so the device can be listed and revoked
    # without storing the token or any other secret.
    session_id = uuid.uuid4().hex
    token, expires_at = create_access_token(
        user.id,
        settings=settings,
        version=user.token_version,
        jti=session_id,
    )
    record_session(
        db,
        user=user,
        session_id=session_id,
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip_address=deps.get_client_ip(request) if settings.session_store_ip else None,
    )
    # A login always counts as activity.
    touch_last_seen(db, user, interval_seconds=0)

    return TokenResponse(
        access_token=token,
        expires_in=access_token_ttl_seconds(settings),
    )


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get the current user",
    description="Return the account that owns the presented access token.",
    responses={401: _UNAUTHORIZED_RESPONSE, 403: _FORBIDDEN_RESPONSE},
)
def read_current_user(current_user: User = Depends(deps.get_current_active_user)) -> User:
    """Return the authenticated, active user."""
    return current_user


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out",
    description=(
        "Revoke the presented access token by recording its ``jti`` in the "
        "server-side denylist. The token can no longer be used even though it "
        "has not expired yet."
    ),
    responses={401: _UNAUTHORIZED_RESPONSE},
)
def logout(
    claims: AccessTokenClaims = Depends(deps.get_token_claims),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Revoke the current access token and close its device session."""
    revoke_token(db, claims, user_id=claims.subject_id)

    # Tokens issued before device sessions existed have no row; the denylist
    # entry above still revokes them.
    session = get_session(db, session_id=claims.jti)
    if session is not None and not session.is_revoked:
        revoke_session(db, session=session)

    return MessageResponse(message="Successfully logged out.")
