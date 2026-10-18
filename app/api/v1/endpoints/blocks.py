"""Block endpoints.

Routes (all under ``/api/v1/blocks``):

* ``POST   /``            — block a user
* ``GET    /``            — page through the users I blocked
* ``DELETE /{username}``  — unblock a user

A block is stored directionally but applied symmetrically: once it exists,
neither account can view the other's profile, add the other as a contact, or
appear in the other's search results or contact list.

Blocking is intentionally non-destructive: it does not delete an existing
contact relationship, so unblocking restores the previous state. This module
does **not** claim to make future messaging safe — those rules must be applied
again in the chat phase.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.errors import NotFoundError
from app.db.models import User
from app.db.session import get_db
from app.schemas.contact import UsernameReference
from app.schemas.user import UserPage, UserPublic, build_public_profile
from app.services import contact as contact_service
from app.services.user import get_user_by_username

router = APIRouter(prefix="/blocks", tags=["blocks"])

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

_NOT_FOUND_RESPONSE = {
    "description": "No such user, or no such block.",
    "content": {
        "application/json": {
            "example": {"error": {"code": "not_found", "message": "User not found."}}
        }
    },
}

_CONFLICT_RESPONSE = {
    "description": "The user is already blocked.",
    "content": {
        "application/json": {
            "example": {"error": {"code": "conflict", "message": "This user is already blocked."}}
        }
    },
}

_BAD_REQUEST_RESPONSE = {
    "description": "The request cannot be carried out (for example blocking yourself).",
    "content": {
        "application/json": {
            "example": {
                "error": {"code": "bad_request", "message": "You cannot block yourself."}
            }
        }
    },
}

_RATE_LIMIT_RESPONSE = {
    "description": "Too many block operations. Retry after the ``Retry-After`` interval.",
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
    "",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Block a user",
    description=(
        "Block the account with the given username. Blocking myself, an unknown "
        "or deactivated user, and a duplicate block are rejected."
    ),
    responses={
        400: _BAD_REQUEST_RESPONSE,
        401: _UNAUTHORIZED_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        409: _CONFLICT_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def block_user(
    payload: UsernameReference,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> UserPublic:
    """Create a block owned by the caller."""
    deps.enforce_rate_limit(request, settings, "contact", f"user:{current_user.id}")

    target = get_user_by_username(db, payload.username)
    if target is None:
        raise NotFoundError("User not found.")

    contact_service.block_user(db, blocker=current_user, blocked=target)
    # The blocker just interacted with the target, so echoing the profile back is
    # safe even though the endpoints that *read* another profile now return 404.
    return build_public_profile(target, last_seen=None)


@router.get(
    "",
    response_model=UserPage,
    summary="List blocked users",
    description="Page through the accounts I have blocked, newest first.",
    responses={401: _UNAUTHORIZED_RESPONSE, 403: _UNAUTHORIZED_RESPONSE, 429: _RATE_LIMIT_RESPONSE},
)
def list_blocked_users(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="Blocks per page."),
    offset: int = Query(default=0, ge=0, description="Blocks to skip."),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> UserPage:
    """Return a page of the caller's blocked users."""
    deps.enforce_rate_limit(request, settings, "contact", f"user:{current_user.id}")

    effective_limit = min(limit, settings.search_max_limit)
    blocked = contact_service.list_blocked(
        db,
        blocker_id=current_user.id,
        limit=effective_limit,
        offset=offset,
    )
    total = contact_service.count_blocked(db, blocker_id=current_user.id)

    return UserPage(
        items=[build_public_profile(user, last_seen=None) for user in blocked],
        total=total,
        limit=effective_limit,
        offset=offset,
    )


@router.delete(
    "/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Unblock a user",
    description="Remove my block on ``username``. 204 on success.",
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def unblock_user(
    username: str,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Remove the caller's block on another account."""
    deps.enforce_rate_limit(request, settings, "contact", f"user:{current_user.id}")

    target = get_user_by_username(db, username)
    if target is None:
        raise NotFoundError("User not found.")

    if not contact_service.unblock_user(db, blocker_id=current_user.id, blocked_id=target.id):
        raise NotFoundError("Block not found.")
