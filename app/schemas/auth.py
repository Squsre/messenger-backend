"""Schemas for authentication endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import PASSWORD_MAX_LENGTH, validate_password_policy

# Length caps keep oversized payloads from reaching the hasher.
_EMAIL_MAX_LENGTH = 255


class UserCreate(BaseModel):
    """Registration payload."""

    email: EmailStr = Field(
        max_length=_EMAIL_MAX_LENGTH,
        description="Account email address. Stored lower-cased.",
        examples=["user@example.com"],
    )
    password: str = Field(
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="Password satisfying the documented policy.",
        examples=["Str0ng-Passw0rd!"],
    )

    @field_validator("password")
    @classmethod
    def _check_policy(cls, value: str) -> str:
        # Raising ValueError here surfaces as a 422 without echoing the value.
        return validate_password_policy(value)


class LoginRequest(BaseModel):
    """Login payload."""

    email: EmailStr = Field(
        max_length=_EMAIL_MAX_LENGTH,
        description="Account email address.",
        examples=["user@example.com"],
    )
    password: str = Field(
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="Account password.",
        examples=["Str0ng-Passw0rd!"],
    )


class UserRead(BaseModel):
    """Public representation of a user. Never includes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    """An issued access token."""

    access_token: str = Field(description="Signed JWT to send as a Bearer token.")
    token_type: Literal["bearer"] = Field(default="bearer")
    expires_in: int = Field(description="Token lifetime in seconds.", examples=[900])


class MessageResponse(BaseModel):
    """Simple acknowledgement payload."""

    message: str
