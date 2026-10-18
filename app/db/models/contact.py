"""One-directional contact relationships.

Adding ``bob`` to ``alice``'s contacts creates exactly one row
``(owner=alice, contact=bob)``. It does **not** make ``alice`` a contact of
``bob``: this phase deliberately implements the simple one-way model, and
mutual connection requests are documented as a later sub-phase (see README).

The pair ``(owner_id, contact_id)`` is unique and self-references are rejected by
a CHECK constraint, so the rules hold even if application code is bypassed.
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


class Contact(Base):
    """``owner`` has added ``contact`` to their contact list."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("owner_id", "contact_id", name="uq_contacts_owner_contact"),
        CheckConstraint("owner_id <> contact_id", name="contacts_no_self"),
        # Listing a user's contacts newest-first is the hot path.
        Index("ix_contacts_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[int] = mapped_column(
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
        return f"<Contact owner={self.owner_id!r} contact={self.contact_id!r}>"
