"""Schemas for contacts and blocks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.security import USERNAME_MAX_LENGTH, USERNAME_MIN_LENGTH


class UsernameReference(BaseModel):
    """Identify a user by username."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        description="Username of the target account.",
        examples=["bob"],
    )
