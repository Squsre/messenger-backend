"""Orphan detection must be conservative and never delete live objects."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import Media
from app.services.media_orphans import scan_media_objects
from app.services.storage import new_storage_key
from tests._media import upload_one
from tests._realtime import conversation_between


def test_orphan_scan_separates_old_and_recent_untracked_objects(
    client, api, app, db_session
) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.create_user("bob@example.com")
    conversation_id = conversation_between(client, alice_headers, "bob")
    upload_one(client, alice_headers, conversation_id)

    storage = app.state.storage
    old_key = new_storage_key("txt")
    recent_key = new_storage_key("txt")
    storage.save(old_key, b"old orphan", content_type="text/plain")
    storage.save(recent_key, b"recent orphan", content_type="text/plain")

    now = datetime.now(timezone.utc)
    old_timestamp = (now - timedelta(hours=2)).timestamp()
    os.utime(storage._path(old_key), (old_timestamp, old_timestamp))

    report = scan_media_objects(
        db_session,
        storage,
        grace_seconds=3600,
        now=now,
    )

    assert report.database_objects == 1
    assert report.storage_objects == 3
    assert report.orphaned_keys == (old_key,)
    assert report.recent_untracked_keys == (recent_key,)
    assert report.missing_keys == ()

    # The safe delete step is explicit; the recent object is not eligible.
    assert storage.delete(old_key) is True
    assert storage.exists(recent_key) is True


def test_orphan_scan_reports_database_rows_with_missing_objects(
    client, api, app, db_session
) -> None:
    _, alice_headers = api.create_user("alice@example.com")
    api.create_user("bob@example.com")
    conversation_id = conversation_between(client, alice_headers, "bob")
    item = upload_one(client, alice_headers, conversation_id)

    media = db_session.execute(
        select(Media).where(Media.public_id == item["id"])
    ).scalar_one()
    app.state.storage.delete(media.storage_key)

    report = scan_media_objects(
        db_session,
        app.state.storage,
        grace_seconds=0,
    )

    assert report.missing_keys == (media.storage_key,)
    assert report.orphaned_keys == ()


def test_orphan_scan_never_deletes_a_key_referenced_by_any_backend(
    client, api, app, db_session
) -> None:
    """A stale/incorrect backend value must not turn a live object into an orphan."""
    _, alice_headers = api.create_user("alice@example.com")
    api.create_user("bob@example.com")
    conversation_id = conversation_between(client, alice_headers, "bob")
    item = upload_one(client, alice_headers, conversation_id)

    media = db_session.execute(
        select(Media).where(Media.public_id == item["id"])
    ).scalar_one()
    media.storage_backend = "minio"
    db_session.commit()

    report = scan_media_objects(db_session, app.state.storage, grace_seconds=0)

    # The local backend does not own this row, so it is not counted as a local
    # database object, but the key is still protected from deletion.
    assert report.database_objects == 0
    assert report.missing_keys == ()
    assert report.orphaned_keys == ()
    assert app.state.storage.exists(media.storage_key) is True
