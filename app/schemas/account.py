"""Schemas for account security: email verification, passwords and sessions."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import PASSWORD_MAX_LENGTH, validate_password_policy

_EMAIL_MAX_LENGTH = 255
#: Opaque tokens are 43 characters today (32 random bytes, url-safe base64).
_TOKEN_MIN_LENGTH = 16
_TOKEN_MAX_LENGTH = 128


class EmailVerificationConfirm(BaseModel):
    """Confirm an email address from a link."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        min_length=_TOKEN_MIN_LENGTH,
        max_length=_TOKEN_MAX_LENGTH,
        description="Token taken from the verification link.",
    )


class PasswordChangeRequest(BaseModel):
    """Change the password of the authenticated account."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="The password in use right now.",
    )
    new_password: str = Field(
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="Replacement password, satisfying the documented policy.",
        examples=["Ev3n-Str0nger!"],
    )

    @field_validator("new_password")
    @classmethod
    def _check_policy(cls, value: str) -> str:
        # Raising here surfaces as a 422 without echoing the submitted value.
        return validate_password_policy(value)


class PasswordResetRequest(BaseModel):
    """Ask for a password-reset link."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(
        max_length=_EMAIL_MAX_LENGTH,
        description="Address to send the link to, if it belongs to an account.",
        examples=["user@example.com"],
    )


class PasswordResetConfirm(BaseModel):
    """Finish a password reset."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        min_length=_TOKEN_MIN_LENGTH,
        max_length=_TOKEN_MAX_LENGTH,
        description="Token taken from the reset link.",
    )
    new_password: str = Field(
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="Replacement password, satisfying the documented policy.",
    )

    @field_validator("new_password")
    @classmethod
    def _check_policy(cls, value: str) -> str:
        return validate_password_policy(value)


class SessionRead(BaseModel):
    """One active device.

    The identifier is the session row's own key, not the token id, so listing
    devices never leaks anything that could be replayed. ``ip_address`` is only
    ever returned to the account that owns the session.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Session identifier used to revoke this device.")
    user_agent: str | None = Field(default=None, description="Truncated device description.")
    ip_address: str | None = Field(default=None, description="Address the login came from.")
    created_at: datetime
    last_active_at: datetime
    expires_at: datetime
    current: bool = Field(
        default=False,
        description="True for the session that made this request.",
    )


class SessionList(BaseModel):
    """All active devices of the authenticated account."""

    items: list[SessionRead]
    total: int = Field(description="Number of active sessions.", examples=[1])
