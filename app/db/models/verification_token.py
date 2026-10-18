"""Single-use tokens for email verification and password reset.

The raw token is **never** stored: only its SHA-256 digest is, so a database
leak cannot be used to verify an email or take over an account. Tokens are
single-use (``used_at``) and time-limited (``expires_at``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VerificationPurpose(StrEnum):
    """What an opaque token may be used for."""

    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


class VerificationToken(Base):
    """A hashed, single-use, expiring token."""

    __tablename__ = "verification_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    # SHA-256 hex digest of the raw token (64 characters).
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<VerificationToken user={self.user_id!r} purpose={self.purpose!r}>"
