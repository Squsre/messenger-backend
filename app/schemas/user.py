"""Profile schemas.

Two representations exist on purpose and must not be confused:

* :class:`UserPublic` — what another account may learn about a user. It never
  contains the email, the password hash, the account state or any token.
* :class:`UserPrivate` — the owner's own view, which adds the fields only the
  account holder may see.

The builders at the bottom are the single place where an ORM :class:`User` is
converted for output, so no endpoint can accidentally serialise a password hash
(``hashed_password`` is simply absent from both models).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.security import (
    BIO_MAX_LENGTH,
    DISPLAY_NAME_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    UsernamePolicyError,
    validate_username,
)
from app.db.models import ProfileVisibility, User


class UserPublic(BaseModel):
    """Public profile of a user."""

    model_config = ConfigDict(from_attributes=True)

    username: str = Field(description="Unique, lower-cased handle.", examples=["alice"])
    display_name: str = Field(description="Name to show; falls back to the username.")
    bio: str | None = Field(default=None, description="Short self-description.")
    avatar_key: str | None = Field(
        default=None,
        description="Opaque storage key of the avatar, or null when unset.",
    )
    profile_visibility: ProfileVisibility = Field(description="Who may view this profile.")
    created_at: datetime = Field(description="When the account was created.")
    last_seen_at: datetime | None = Field(
        default=None,
        description="Last activity, or null when the owner hides it.",
    )


class UserPrivate(UserPublic):
    """The account owner's own view of their profile."""

    id: int
    email: EmailStr
    display_name: str | None = Field(
        default=None,
        description="Null when the owner has not chosen one; the public view then shows the username.",
    )
    is_active: bool = Field(description="Whether the account may sign in.")
    email_verified: bool = Field(description="Whether the email address is confirmed.")
    show_last_seen: bool = Field(description="Whether last-seen is shared publicly.")
    updated_at: datetime


class ProfileUpdate(BaseModel):
    """Fields a user may change about their own profile.

    ``extra="forbid"`` is what makes the protected fields protected: sending
    ``id``, ``email``, ``is_active``, ``token_version``, ``hashed_password`` or
    ``avatar_key`` is a 422 rather than a silent no-op.
    """

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(
        default=None,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        description="New handle (lower-case letters, digits and underscores).",
    )
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX_LENGTH)
    bio: str | None = Field(default=None, max_length=BIO_MAX_LENGTH)
    profile_visibility: ProfileVisibility | None = None
    show_last_seen: bool | None = None

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str | None) -> str | None:
        if value is None:
            raise UsernamePolicyError("Username cannot be cleared.")
        return validate_username(value)

    @model_validator(mode="after")
    def _require_a_change(self) -> ProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class PrivacySettingsUpdate(BaseModel):
    """The privacy subset of the profile, for a dedicated settings screen."""

    model_config = ConfigDict(extra="forbid")

    profile_visibility: ProfileVisibility | None = None
    show_last_seen: bool | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> PrivacySettingsUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one setting to update.")
        return self


class UserPage(BaseModel):
    """A page of public profiles plus the paging metadata."""

    items: list[UserPublic]
    total: int = Field(description="Total matches for the query.", examples=[1])
    limit: int = Field(description="Maximum items requested.")
    offset: int = Field(description="Number of matches skipped.")


def build_public_profile(user: User, *, last_seen: datetime | None) -> UserPublic:
    """Convert a user to its public representation."""
    return UserPublic(
        username=user.username,
        display_name=user.display_name or user.username,
        bio=user.bio,
        avatar_key=user.avatar_key,
        profile_visibility=ProfileVisibility(user.profile_visibility),
        created_at=user.created_at,
        last_seen_at=last_seen,
    )


def build_private_profile(user: User) -> UserPrivate:
    """Convert a user to its owner-only representation."""
    return UserPrivate(
        id=user.id,
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        bio=user.bio,
        avatar_key=user.avatar_key,
        profile_visibility=ProfileVisibility(user.profile_visibility),
        show_last_seen=user.show_last_seen,
        is_active=user.is_active,
        email_verified=user.is_email_verified,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_seen_at=user.last_seen_at,
    )
