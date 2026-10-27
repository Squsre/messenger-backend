"""Safe detection of media objects that are not referenced by PostgreSQL.

PostgreSQL is the source of truth. An object is only eligible for deletion when:

* its key is absent from every ``media.storage_key`` row, **and**
* it is older than a configurable grace period, so an upload that is still
  between the object write and the database commit is never swept away.

The report also lists database rows whose object is missing. Those rows are not
deleted automatically: an operator may need the metadata for recovery. Orphan
detection checks *all* database keys (even a row with an inconsistent backend
value), while missing-object reporting is scoped to the backend being inspected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Media
from app.services.storage import StorageService


@dataclass(frozen=True)
class MediaOrphanReport:
    """A read-only snapshot; deletion is a separate, explicit operation."""

    storage_objects: int
    database_objects: int
    orphaned_keys: tuple[str, ...]
    recent_untracked_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]


def scan_media_objects(
    db: Session,
    storage: StorageService,
    *,
    prefix: str = "",
    grace_seconds: int = 3600,
    now: datetime | None = None,
) -> MediaOrphanReport:
    """Compare object storage with the ``media`` table without mutating either."""
    if grace_seconds < 0:
        raise ValueError("grace_seconds must not be negative")
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(seconds=grace_seconds)

    # Orphan safety uses *every* database key, even one whose backend column
    # looks inconsistent, so a valid object is never deleted because of a
    # metadata mismatch. Missing-object reporting is scoped to rows owned by the
    # backend being inspected.
    all_database_keys = set(db.execute(select(Media.storage_key)).scalars().all())
    backend_keys = set(
        db.execute(
            select(Media.storage_key).where(
                Media.storage_backend == storage.backend_name
            )
        )
        .scalars()
        .all()
    )
    objects = list(storage.iter_objects(prefix))
    storage_keys = {item.key for item in objects}

    orphaned = sorted(
        item.key
        for item in objects
        if item.key not in all_database_keys and item.last_modified <= cutoff
    )
    recent = sorted(
        item.key
        for item in objects
        if item.key not in all_database_keys and item.last_modified > cutoff
    )
    missing = sorted(backend_keys - storage_keys)

    return MediaOrphanReport(
        storage_objects=len(objects),
        database_objects=len(backend_keys),
        orphaned_keys=tuple(orphaned),
        recent_untracked_keys=tuple(recent),
        missing_keys=tuple(missing),
    )
