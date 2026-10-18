"""Infrastructure model used to verify database connectivity.

This is deliberately *not* a messaging entity. It records the outcome of a
health probe so that the database layer can be exercised end-to-end (write then
read) without introducing domain concepts that belong to later stages.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class HealthCheck(TimestampMixin, Base):
    """A single recorded health probe."""

    __tablename__ = "health_checks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<HealthCheck id={self.id!r} status={self.status!r}>"
