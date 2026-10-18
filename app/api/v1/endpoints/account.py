"""Account security endpoints: email verification, passwords and devices.

Routes (all under ``/api/v1/auth``):

* ``POST   /email/verify/request`` — (re)send the verification link
* ``POST   /email/verify/confirm`` — confirm an email address from a link
* ``POST   /password/change``      — change the password (authenticated)
* ``POST   /password/reset/request`` — ask for a reset link
* ``POST   /password/reset/confirm`` — finish a reset
* ``GET    /sessions``             — list the account's active devices
* ``DELETE /sessions/{id}``        — end one device
* ``POST   /logout/all``           — end every device

Deliberate design points:

* Password change/reset advances ``User.token_version``, which invalidates every
  previously issued access token, and closes every device session. A password
  change then mints a fresh token for the device that made the request so the
  caller is not logged out of their own session.
* ``POST /password/reset/request`` answers identically for a registered and an
  unknown address, so it cannot be used to test whether an email exists.
* Tokens are only ever sent by email; they are never returned by the API.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.errors import BadRequestError, NotFoundError
from app.core.security import (
    AccessTokenClaims,
    access_token_ttl_seconds,
    create_access_token,
    verify_password,
)
from app.db.models import User, VerificationPurpose
from app.db.session import get_db
from app.schemas.account import (
    EmailVerificationConfirm,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    SessionList,
    SessionRead,
)
from app.schemas.auth import MessageResponse, TokenResponse
from app.services import verification as verification_service
from app.services.email import EmailService
from app.services.session import (
    get_user_session,
    list_active_sessions,
    prune_finished_sessions,
    record_session,
    revoke_all_sessions,
    revoke_session,
)
from app.services.user import get_user_by_email, normalize_email, set_password

router = APIRouter(prefix="/auth", tags=["account"])

#: Identical answer for "link sent" and "no such account".
RESET_REQUEST_MESSAGE = (
    "If that email address belongs to an account, a password reset link has been sent."
)

_VERIFICATION_REQUEST_MESSAGE = (
    "If the address still needs confirming, a verification link has been sent."
)

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

_BAD_REQUEST_RESPONSE = {
    "description": "The request is well-formed but cannot be carried out.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "bad_request",
                    "message": "This link is invalid or has expired.",
                }
            }
        }
    },
}

_NOT_FOUND_RESPONSE = {
    "description": "No such session for this account.",
    "content": {
        "application/json": {
            "example": {"error": {"code": "not_found", "message": "Session not found."}}
        }
    },
}

_RATE_LIMIT_RESPONSE = {
    "description": "Too many attempts. Retry after the ``Retry-After`` interval.",
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _issue_device_token(
    db: Session,
    *,
    user: User,
    request: Request,
    settings: Settings,
) -> TokenResponse:
    """Mint an access token plus its device session for ``user``.

    Shared by login and password change so both paths record the device the same
    way and never store the token itself.
    """
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
    return TokenResponse(access_token=token, expires_in=access_token_ttl_seconds(settings))


# --- email verification -----------------------------------------------------


@router.post(
    "/email/verify/request",
    response_model=MessageResponse,
    summary="Send (or resend) the email verification link",
    description=(
        "Send a verification link to the authenticated account's address. The "
        "answer is the same whether or not the address still needs confirming, "
        "and the token is never included in the response. Throttled per account "
        "and per client address."
    ),
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def request_email_verification(
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    email_service: EmailService = Depends(deps.get_email_service),
) -> MessageResponse:
    """Create a verification token and email the link."""
    deps.enforce_rate_limit(
        request,
        settings,
        "email_verification",
        f"user:{current_user.id}",
        f"ip:{deps.get_client_ip(request) or 'unknown'}",
    )

    verification_service.issue_email_verification(
        db,
        user=current_user,
        settings=settings,
        email_service=email_service,
    )
    return MessageResponse(message=_VERIFICATION_REQUEST_MESSAGE)


@router.post(
    "/email/verify/confirm",
    response_model=MessageResponse,
    summary="Confirm an email address",
    description=(
        "Consume a verification token. The token is single-use and expires; an "
        "unknown, expired or already-used token gets the same 400 answer."
    ),
    responses={400: _BAD_REQUEST_RESPONSE, 429: _RATE_LIMIT_RESPONSE},
)
def confirm_email_verification(
    payload: EmailVerificationConfirm,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Mark the token owner's address as verified."""
    deps.enforce_rate_limit(
        request,
        settings,
        "email_verify_confirm",
        f"ip:{deps.get_client_ip(request) or 'unknown'}",
    )
    verification_service.confirm_email_verification(db, raw_token=payload.token)
    return MessageResponse(message="Email address verified.")


# --- password change --------------------------------------------------------


@router.post(
    "/password/change",
    response_model=TokenResponse,
    summary="Change my password",
    description=(
        "Change the authenticated account's password. The current password must "
        "be supplied. On success every other access token and device session is "
        "invalidated (``token_version`` is advanced) and a fresh token is "
        "returned for the caller's device.\n\n"
        "Weak passwords are rejected with 422; the submitted values are never "
        "echoed back."
    ),
    responses={
        400: _BAD_REQUEST_RESPONSE,
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        422: {
            "description": "The new password does not satisfy the policy.",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "code": "validation_error",
                            "message": "Request validation failed.",
                            "details": [
                                {"type": "value_error", "loc": ["body", "new_password"], "msg": "..."}
                            ],
                        }
                    }
                }
            },
        },
    },
)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    claims: AccessTokenClaims = Depends(deps.get_token_claims),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> TokenResponse:
    """Replace the password and end every other session."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise BadRequestError("Current password is incorrect.")

    if verify_password(payload.new_password, current_user.hashed_password):
        raise BadRequestError("The new password must differ from the current one.")

    # Advancing token_version invalidates every outstanding access token.
    set_password(db, current_user, payload.new_password)
    revoke_all_sessions(db, user=current_user, bump_token_version=False)

    return _issue_device_token(db, user=current_user, request=request, settings=settings)


# --- password reset ---------------------------------------------------------


@router.post(
    "/password/reset/request",
    response_model=MessageResponse,
    summary="Request a password reset link",
    description=(
        "Send a reset link when the address belongs to an active account. The "
        "response is identical for a registered and an unknown address, so this "
        "endpoint cannot be used to discover accounts. Throttled per address and "
        "per client address."
    ),
    responses={422: {"description": "Malformed email address."}, 429: _RATE_LIMIT_RESPONSE},
)
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    email_service: EmailService = Depends(deps.get_email_service),
) -> MessageResponse:
    """Email a reset link, always answering the same way."""
    deps.enforce_rate_limit(
        request,
        settings,
        "password_reset",
        f"email:{normalize_email(payload.email)}",
        f"ip:{deps.get_client_ip(request) or 'unknown'}",
    )

    user = get_user_by_email(db, payload.email)
    if user is not None and user.is_active:
        verification_service.issue_password_reset(
            db,
            user=user,
            settings=settings,
            email_service=email_service,
        )
        verification_service.prune_expired_tokens(db)

    return MessageResponse(message=RESET_REQUEST_MESSAGE)


@router.post(
    "/password/reset/confirm",
    response_model=MessageResponse,
    summary="Complete a password reset",
    description=(
        "Consume a reset token and set the new password. The token is single-use "
        "and expires. Every access token and device session is invalidated, so "
        "the account must sign in again with the new password."
    ),
    responses={400: _BAD_REQUEST_RESPONSE, 429: _RATE_LIMIT_RESPONSE},
)
def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MessageResponse:
    """Burn the token, set the password and revoke all sessions."""
    deps.enforce_rate_limit(
        request,
        settings,
        "password_reset_confirm",
        f"ip:{deps.get_client_ip(request) or 'unknown'}",
    )

    token = verification_service.consume_token(
        db,
        raw_token=payload.token,
        purpose=VerificationPurpose.PASSWORD_RESET,
    )
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise BadRequestError(verification_service.INVALID_TOKEN_MESSAGE)

    set_password(db, user, payload.new_password)
    revoke_all_sessions(db, user=user, bump_token_version=False)

    return MessageResponse(message="Your password has been reset. Please sign in again.")


# --- sessions and devices ---------------------------------------------------


@router.get(
    "/sessions",
    response_model=SessionList,
    summary="List my active devices",
    description=(
        "Return every session that is still usable, most recently active first. "
        "The identifier is the session's own key (never the token id or any "
        "secret) and is used to revoke that device. Only the account owner sees "
        "their own sessions."
    ),
    responses={401: _UNAUTHORIZED_RESPONSE, 403: _UNAUTHORIZED_RESPONSE},
)
def list_my_sessions(
    current_user: User = Depends(deps.get_current_active_user),
    claims: AccessTokenClaims = Depends(deps.get_token_claims),
    db: Session = Depends(get_db),
) -> SessionList:
    """Return the caller's active device sessions."""
    prune_finished_sessions(db, user_id=current_user.id)
    sessions = list_active_sessions(db, user_id=current_user.id)

    items = [
        SessionRead(
            id=session.id,
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            created_at=session.created_at,
            last_active_at=session.last_active_at,
            expires_at=session.expires_at,
            current=session.session_id == claims.jti,
        )
        for session in sessions
    ]
    return SessionList(items=items, total=len(items))


@router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
    summary="End one device",
    description=(
        "Revoke a single session by the id returned from ``GET /auth/sessions``. "
        "The token denylist rejects the affected token on its next use. A session "
        "belonging to another account is reported as 404."
    ),
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
    },
)
def delete_session(
    session_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Revoke one of the caller's sessions."""
    session = get_user_session(db, user_id=current_user.id, session_pk=session_id)
    if session is None or session.is_revoked:
        raise NotFoundError("Session not found.")

    expires_at = session.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= _utcnow():
        raise NotFoundError("Session not found.")

    revoke_session(db, session=session)
    return MessageResponse(message="Session revoked.")


@router.post(
    "/logout/all",
    response_model=MessageResponse,
    summary="Log out of all devices",
    description=(
        "End every session and invalidate every outstanding access token for the "
        "account, including tokens issued before device sessions existed "
        "(``token_version`` is advanced). The token used to make this request is "
        "invalidated as well."
    ),
    responses={401: _UNAUTHORIZED_RESPONSE, 403: _UNAUTHORIZED_RESPONSE},
)
def logout_all_devices(
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Invalidate every token and close every session of the account."""
    closed = revoke_all_sessions(db, user=current_user)
    return MessageResponse(message=f"Signed out of {closed} session(s).")
