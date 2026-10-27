"""Report and optionally delete unreferenced media objects.

Usage (dry-run is the default)::

    python -m app.tools.media_orphans
    python -m app.tools.media_orphans --grace-seconds 3600
    python -m app.tools.media_orphans --delete

The tool uses the application's configured database and storage backend. It
never deletes a database row and never deletes a recently written object, so an
in-flight upload cannot be mistaken for an orphan.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Media
from app.db.session import Database
from app.services.media_orphans import scan_media_objects
from app.services.storage import StorageError, build_storage


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        default="",
        help="Only inspect storage keys with this prefix.",
    )
    parser.add_argument(
        "--grace-seconds",
        type=int,
        default=3600,
        help="Minimum object age before it is eligible for deletion (default 3600).",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete reported orphans. Without this flag the tool is read-only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.grace_seconds < 0:
        raise SystemExit("--grace-seconds must not be negative")

    settings = get_settings()
    database = Database(settings.database_url, echo=settings.db_echo)
    storage = build_storage(settings)
    try:
        with database.session() as db:
            report = scan_media_objects(
                db,
                storage,
                prefix=args.prefix,
                grace_seconds=args.grace_seconds,
            )
        print(json.dumps(asdict(report), indent=2, sort_keys=True))

        if not args.delete:
            return 0

        deleted: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        with database.session() as db:
            for key in report.orphaned_keys:
                # Re-check immediately before deletion. This narrows the race
                # with an in-flight upload/attachment without ever trusting a
                # stale report.
                referenced = db.execute(
                    select(Media.id).where(Media.storage_key == key)
                ).first()
                if referenced is not None:
                    skipped.append(key)
                    continue
                try:
                    storage.delete(key)
                    deleted.append(key)
                except StorageError:
                    failed.append(key)
        print(
            json.dumps(
                {
                    "deleted": deleted,
                    "skipped_referenced": skipped,
                    "failed": failed,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1 if failed else 0
    finally:
        storage.close()
        database.dispose()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
