"""User model."""

from __future__ import annotations

from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    """A registered account.

    Only the Argon2id hash of the password is stored — never the raw value.

    ``email`` is stored already normalised (trimmed and lower-cased) by
    :func:`app.services.user.normalize_email`, and carries a unique constraint
    so duplicates cannot be created even under a race.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<User id={self.id!r} email={self.email!r} active={self.is_active!r}>"
