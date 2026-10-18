"""User-to-user blocks.

A block is directional in storage (``blocker`` → ``blocked``) but symmetric in
effect at the privacy layer: neither party can see the other's profile or
interact. The pair is unique and self-blocks are rejected by a CHECK constraint.

Blocks are enforced in profiles, search, contacts and (later) messaging. This
model alone does not make the future chat safe — those rules must be applied
again when messaging is implemented.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Block(Base):
    """``blocker`` has blocked ``blocked``."""

    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_blocks_blocker_blocked"),
        CheckConstraint("blocker_id <> blocked_id", name="blocks_no_self"),
        Index("ix_blocks_blocker_created", "blocker_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    blocker_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    blocked_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Block blocker={self.blocker_id!r} blocked={self.blocked_id!r}>"
