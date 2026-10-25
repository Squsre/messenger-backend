"""User model."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ProfileVisibility(StrEnum):
    """Who may view a full public profile."""

    PUBLIC = "public"
    CONTACTS = "contacts"
    PRIVATE = "private"


#: Stored as a plain string with a CHECK constraint (avoids PostgreSQL-native
#: ENUM type migrations).
PROFILE_VISIBILITY_VALUES: tuple[str, ...] = tuple(v.value for v in ProfileVisibility)

_VISIBILITY_SQL_LIST = ", ".join(f"'{value}'" for value in PROFILE_VISIBILITY_VALUES)


class User(TimestampMixin, Base):
    """A registered account.

    Only the Argon2id hash of the password is stored — never the raw value.

    Both ``email`` and ``username`` are stored already normalised (trimmed and
    lower-cased) by :mod:`app.services.user`, and each carries a unique index so
    duplicates cannot be created even under a race, and so the uniqueness rule
    is case-insensitive in PostgreSQL.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"profile_visibility IN ({_VISIBILITY_SQL_LIST})",
            name="profile_visibility_valid",
        ),
        CheckConstraint("token_version >= 0", name="token_version_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    # --- Profile ---
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(280), nullable=True)
    # Opaque object-storage key, never a user-supplied URL. See app.services.profile.
    avatar_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_visibility: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ProfileVisibility.PUBLIC.value,
        server_default=ProfileVisibility.PUBLIC.value,
    )
    show_last_seen: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Account security ---
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Bumped to invalidate every previously issued access token at once
    # (password change / reset / "log out everywhere").
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    @property
    def is_email_verified(self) -> bool:
        """Whether the account's email address has been confirmed."""
        return self.email_verified_at is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<User id={self.id!r} username={self.username!r} active={self.is_active!r}>"
