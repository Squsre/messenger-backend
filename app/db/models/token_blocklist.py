"""Denylist of revoked access tokens.

JWTs are stateless, so a token stays valid until it expires unless the server
records that it was revoked. This model is that record: on logout the token's
``jti`` is stored here and every authenticated request checks it. Entries can
be deleted once the token has expired, which keeps the table small.

This is a real, verifiable revocation mechanism that needs no extra
infrastructure beyond the existing database. See ``README.md`` for its
trade-offs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TokenBlocklist(Base):
    """A revoked token identifier."""

    __tablename__ = "token_blocklist"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    token_type: Mapped[str] = mapped_column(String(16), nullable=False, default="access")
    # When the token would have expired anyway; used to prune the table.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<TokenBlocklist jti={self.jti!r}>"
