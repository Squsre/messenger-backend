"""Profile endpoints.

Routes (all under ``/api/v1/users``):

* ``GET   /me``         — the authenticated account's own profile
* ``PATCH /me``         — edit the editable profile fields
* ``PATCH /me/privacy`` — the privacy subset (for a settings screen)
* ``GET   /search``     — find users by username prefix
* ``GET   /{username}`` — view another account's public profile

Route order matters: ``/me``, ``/me/privacy`` and ``/search`` are declared
before ``/{username}`` so they are never captured as a username.

Protected fields (``id``, ``email``, ``is_active``, ``hashed_password``,
``token_version``, ``avatar_key``) cannot be written through this API — the
update schemas forbid unknown fields, so an attempt is a 422.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.errors import BadRequestError, NotFoundError
from app.db.models import User
from app.db.session import get_db
from app.schemas.user import (
    PrivacySettingsUpdate,
    ProfileUpdate,
    UserPage,
    UserPrivate,
    UserPublic,
    build_private_profile,
    build_public_profile,
)
from app.services import contact as contact_service
from app.services.profile import (
    can_view_profile,
    update_profile,
    visible_last_seen,
)
from app.services.user import get_user_by_username
from app.services.user import normalize_username

router = APIRouter(prefix="/users", tags=["users"])

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
    "description": (
        "No viewable account with that username. Also returned for a deactivated "
        "account, a profile the caller may not see, and a user on either side of "
        "a block."
    ),
    "content": {
        "application/json": {
            "example": {"error": {"code": "not_found", "message": "User not found."}}
        }
    },
}

_VALIDATION_RESPONSE = {
    "description": "The submitted payload is invalid (unknown or out-of-range field).",
    "content": {
        "application/json": {
            "example": {
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": [{"type": "extra_forbidden", "loc": ["body", "avatar_key"], "msg": "..."}],
                }
            }
        }
    },
}

_CONFLICT_RESPONSE = {
    "description": "That username is already taken (comparison is case-insensitive).",
    "content": {
        "application/json": {
            "example": {"error": {"code": "conflict", "message": "That username is already taken."}}
        }
    },
}

_RATE_LIMIT_RESPONSE = {
    "description": "Too many lookups. Retry after the ``Retry-After`` interval.",
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


@router.get(
    "/me",
    response_model=UserPrivate,
    summary="Get my profile",
    description=(
        "Return the full profile of the authenticated account, including fields "
        "only the owner may see (email, verification state, privacy settings)."
    ),
    responses={401: _UNAUTHORIZED_RESPONSE, 403: _UNAUTHORIZED_RESPONSE},
)
def read_my_profile(current_user: User = Depends(deps.get_current_active_user)) -> UserPrivate:
    """Return the caller's own profile."""
    return build_private_profile(current_user)


@router.patch(
    "/me",
    response_model=UserPrivate,
    summary="Update my profile",
    description=(
        "Update any of: ``username``, ``display_name``, ``bio``, "
        "``profile_visibility``, ``show_last_seen``. At least one field is "
        "required. Protected fields are rejected with 422. An empty string "
        "clears ``display_name`` or ``bio``."
    ),
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        409: _CONFLICT_RESPONSE,
        422: _VALIDATION_RESPONSE,
    },
)
def update_my_profile(
    payload: ProfileUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
) -> UserPrivate:
    """Apply the allow-listed changes to the caller's profile."""
    changes = payload.model_dump(exclude_unset=True)
    update_profile(db, current_user, changes)
    return build_private_profile(current_user)


@router.patch(
    "/me/privacy",
    response_model=UserPrivate,
    summary="Update my privacy settings",
    description=(
        "Change ``profile_visibility`` (``public`` | ``contacts`` | ``private``) "
        "and/or ``show_last_seen``. Equivalent to the privacy fields of "
        "``PATCH /users/me``."
    ),
    responses={401: _UNAUTHORIZED_RESPONSE, 403: _UNAUTHORIZED_RESPONSE, 422: _VALIDATION_RESPONSE},
)
def update_my_privacy(
    payload: PrivacySettingsUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
) -> UserPrivate:
    """Apply the privacy subset of the profile."""
    update_profile(db, current_user, payload.model_dump(exclude_unset=True))
    return build_private_profile(current_user)


@router.get(
    "/search",
    response_model=UserPage,
    summary="Search users by username",
    description=(
        "Prefix search over usernames. The term is lower-cased and the shortest "
        "accepted length is enforced server-side to prevent wide enumeration. "
        "Deactivated accounts and users whose profile is ``private`` are never "
        "returned, and neither is anyone on either side of a block.\n\n"
        "Results contain only public profile fields."
    ),
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        429: _RATE_LIMIT_RESPONSE,
    },
)
def search_users(
    request: Request,
    q: str = Query(
        min_length=1,
        max_length=64,
        description="Username prefix to look for.",
        examples=["al"],
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Results per page."),
    offset: int = Query(default=0, ge=0, description="Results to skip."),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> UserPage:
    """Return a page of users whose username starts with ``q``."""
    deps.enforce_rate_limit(request, settings, "search", f"user:{current_user.id}")

    query = normalize_username(q)
    if len(query) < settings.search_min_query_length:
        raise BadRequestError(
            f"Search term must be at least {settings.search_min_query_length} characters."
        )

    effective_limit = min(limit, settings.search_max_limit)
    matches = contact_service.search_users(
        db,
        query=query,
        viewer_id=current_user.id,
        limit=effective_limit,
        offset=offset,
    )
    total = contact_service.count_search_results(db, query=query, viewer_id=current_user.id)

    return UserPage(
        items=[build_public_profile(user, last_seen=visible_last_seen(viewer=current_user, target=user)) for user in matches],
        total=total,
        limit=effective_limit,
        offset=offset,
    )


@router.get(
    "/{username}",
    response_model=UserPublic,
    summary="View a user's public profile",
    description=(
        "Return another account's public profile. Requires authentication so "
        "``contacts``-only profiles and blocks can be honoured.\n\n"
        "The response never contains an email address, password hash, account "
        "state or token. A profile the caller may not see is reported as 404 so "
        "the endpoint does not reveal that the account exists."
    ),
    responses={
        401: _UNAUTHORIZED_RESPONSE,
        403: _UNAUTHORIZED_RESPONSE,
        404: _NOT_FOUND_RESPONSE,
    },
)
def read_user_profile(
    username: str,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
) -> UserPublic:
    """Return the public profile of ``username``."""
    target = get_user_by_username(db, username)
    if target is None or not target.is_active:
        raise NotFoundError("User not found.")

    if not can_view_profile(db, viewer=current_user, target=target):
        raise NotFoundError("User not found.")

    return build_public_profile(
        target,
        last_seen=visible_last_seen(viewer=current_user, target=target),
    )
