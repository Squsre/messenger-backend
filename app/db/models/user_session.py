"""Per-device sessions.

A session row is created on every successful login and is identified by the
**same random id as the access token's ``jti``** — no token or secret is ever
stored, so a database leak cannot be replayed. That single identifier is enough
to list, inspect and revoke a device.

Revocation of one session is performed through the existing token denylist; the
row additionally records that the session is finished so it disappears from the
user's device list.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: User-Agent strings are truncated to this length before storage.
USER_AGENT_MAX_LENGTH = 255
#: IP addresses are stored only for the account owner's own device list.
IP_ADDRESS_MAX_LENGTH = 45  # enough for IPv6


class UserSession(Base):
    """One logged-in device."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Random hex id, identical to the access token's ``jti`` claim.
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_MAX_LENGTH), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(IP_ADDRESS_MAX_LENGTH), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<UserSession id={self.id!r} user={self.user_id!r} revoked={self.is_revoked!r}>"
