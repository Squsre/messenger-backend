"""Contact endpoints.

Routes (all under ``/api/v1/contacts``):

* ``POST   /``            — add a user to my contacts
* ``GET    /``            — page through my contacts
* ``DELETE /{username}``  — remove a user from my contacts

The relationship is **one-directional** (documented in the README): adding
``bob`` to my contacts creates one row and does not make me a contact of
``bob``, so there is no request/accept handshake in this phase.
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
from app.services.profile import visible_last_seen
from app.services.user import get_user_by_username

router = APIRouter(prefix="/contacts", tags=["contacts"])

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
    "description": "No such user, or no such contact relationship.",
    "content": {
        "application/json": {
            "example": {"error": {"code": "not_found", "message": "User not found."}}
        }
    },
}

_CONFLICT_RESPONSE = {
    "description": "The user is already in my contacts.",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "conflict",
                    "message": "This user is already in your contacts.",
                }
            }
        }
    },
}

_FORBIDDEN_RESPONSE = {
    "description": "A block exists between the two accounts, so the action is refused.",
    "content": {
        "application/json": {
            "example": {"error": {"code": "forbidden", "message": "This user is not available."}}
        }
    },
}

_BAD_REQUEST_RESPONSE = {
    "description": "The request cannot be carried out (for example adding yourself).",
    "content": {
        "application/json": {
            "example": {
                "error": {"code": "bad_request", "message": "You cannot add yourself as a contact."}
            }
        }
    },
}

_RATE_LIMIT_RESPONSE = {
    "description": "Too many contact operations. Retry after the ``Retry-After`` interval.",
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
    summary="Add a contact",
    description=(
        "Add the account with the given username to my contacts. Duplicates, "
        "adding myself, an unknown or deactivated user, and any pair separated "
        "by a block are all rejected."
    ),
    responses={
        400: _BAD_REQUEST_RESPONSE,
        401: _UNAUTHORIZED_RESPONSE,
        403: _FORBIDDEN_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        409: _CONFLICT_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def add_contact(
    payload: UsernameReference,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> UserPublic:
    """Create a one-directional contact relationship."""
    deps.enforce_rate_limit(request, settings, "contact", f"user:{current_user.id}")

    target = get_user_by_username(db, payload.username)
    if target is None:
        raise NotFoundError("User not found.")

    contact_service.add_contact(db, owner=current_user, target=target)
    return build_public_profile(
        target,
        last_seen=visible_last_seen(viewer=current_user, target=target),
    )


@router.get(
    "",
    response_model=UserPage,
    summary="List my contacts",
    description=(
        "Page through my contacts, newest first. Only public profile fields are "
        "returned. Users on either side of a block are omitted (the underlying "
        "relationship is kept, so unblocking restores the list)."
    ),
    responses={401: _UNAUTHORIZED_RESPONSE, 403: _UNAUTHORIZED_RESPONSE, 429: _RATE_LIMIT_RESPONSE},
)
def list_contacts(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="Contacts per page."),
    offset: int = Query(default=0, ge=0, description="Contacts to skip."),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> UserPage:
    """Return a page of the caller's contacts."""
    deps.enforce_rate_limit(request, settings, "contact", f"user:{current_user.id}")

    effective_limit = min(limit, settings.search_max_limit)
    users = contact_service.list_contacts(
        db,
        owner_id=current_user.id,
        limit=effective_limit,
        offset=offset,
    )
    total = contact_service.count_contacts(db, owner_id=current_user.id)

    return UserPage(
        items=[
            build_public_profile(user, last_seen=visible_last_seen(viewer=current_user, target=user))
            for user in users
        ],
        total=total,
        limit=effective_limit,
        offset=offset,
    )


@router.delete(
    "/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Remove a contact",
    description="Delete the contact relationship with ``username``. 204 on success.",
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def remove_contact(
    username: str,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Remove a contact relationship owned by the caller."""
    deps.enforce_rate_limit(request, settings, "contact", f"user:{current_user.id}")

    target = get_user_by_username(db, username)
    if target is None:
        raise NotFoundError("User not found.")

    if not contact_service.remove_contact(db, owner_id=current_user.id, contact_id=target.id):
        raise NotFoundError("Contact not found.")
