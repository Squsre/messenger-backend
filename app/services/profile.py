"""Profile reads, updates, visibility rules and the last-seen throttle.

Profile *data* lives on :class:`~app.db.models.User`; this module holds the rules
around changing and exposing it so endpoints stay thin.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.security import validate_avatar_key, validate_username
from app.db.models import ProfileVisibility, User
from app.services.contact import is_blocked_between, is_contact
from app.services.user import username_is_taken


def utcnow() -> datetime:
    """Timezone-aware ``now`` (the single clock used by the account services)."""
    return datetime.now(timezone.utc)


def as_aware(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime.

    SQLite does not persist timezone offsets on ``DateTime(timezone=True)``
    columns, so values read back can be naive; comparing them with an aware
    ``now`` would raise. PostgreSQL keeps the offset, so this is a no-op there.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


#: Profile fields a user is allowed to change through the API. Anything else
#: (id, email, is_active, token_version, hashed_password, ...) is rejected before
#: it ever reaches this module.
EDITABLE_PROFILE_FIELDS: frozenset[str] = frozenset(
    {"username", "display_name", "bio", "profile_visibility", "show_last_seen"}
)


def resolve_display_name(user: User) -> str:
    """The name to show for a user, falling back to their username."""
    return user.display_name or user.username


def update_profile(db: Session, user: User, changes: dict[str, Any]) -> User:
    """Apply validated profile ``changes`` (keys are already allow-listed)."""
    unknown = set(changes) - EDITABLE_PROFILE_FIELDS
    if unknown:  # pragma: no cover - defensive: the schema forbids extras
        raise ValueError(f"Unsupported profile fields: {sorted(unknown)}")

    if (username := changes.get("username")) is not None:
        normalised = validate_username(username)
        if username_is_taken(db, normalised, exclude_user_id=user.id):
            raise ConflictError("That username is already taken.")
        user.username = normalised

    for field in ("display_name", "bio"):
        if field in changes:
            raw = changes[field]
            # An empty (or whitespace-only) value clears the field.
            user.__setattr__(field, raw.strip() or None if isinstance(raw, str) else None)

    if (visibility := changes.get("profile_visibility")) is not None:
        user.profile_visibility = str(visibility)

    if (show_last_seen := changes.get("show_last_seen")) is not None:
        user.show_last_seen = bool(show_last_seen)

    db.flush()
    return user


def can_view_profile(db: Session, *, viewer: User | None, target: User) -> bool:
    """Whether ``viewer`` may see ``target``'s full public profile.

    * the owner always sees their own profile,
    * ``public``   — anyone (the route still requires authentication),
    * ``contacts`` — only users who have ``target`` in their contact list,
    * ``private``  — nobody else.

    A block in either direction denies access, whatever the visibility level.
    """
    if viewer is not None and viewer.id == target.id:
        return True

    if viewer is None:
        return target.profile_visibility == ProfileVisibility.PUBLIC.value

    if is_blocked_between(db, viewer.id, target.id):
        return False

    if target.profile_visibility == ProfileVisibility.PUBLIC.value:
        return True
    if target.profile_visibility == ProfileVisibility.CONTACTS.value:
        return is_contact(db, owner_id=viewer.id, contact_id=target.id)
    return False


def visible_last_seen(*, viewer: User | None, target: User) -> datetime | None:
    """``target``'s last-seen timestamp, or ``None`` when it must stay hidden."""
    if viewer is not None and viewer.id == target.id:
        return target.last_seen_at
    if not target.show_last_seen:
        return None
    return target.last_seen_at


def touch_last_seen(db: Session, user: User, *, interval_seconds: int) -> bool:
    """Refresh ``last_seen_at``, at most once per ``interval_seconds``.

    Returns whether a write happened. The throttle keeps an activity heartbeat
    from turning every authenticated request into a database write.
    """
    now = utcnow()
    previous = as_aware(user.last_seen_at)
    if interval_seconds > 0 and previous is not None:
        if now - previous < timedelta(seconds=interval_seconds):
            return False
    user.last_seen_at = now
    db.flush()
    return True


def set_avatar_key(db: Session, user: User, avatar_key: str | None) -> User:
    """Attach a validated storage key to a profile.

    Not reachable from the public API: the key is issued by the (future) upload
    service, which calls this function once an image has been accepted. Passing
    ``None`` clears the avatar.
    """
    user.avatar_key = validate_avatar_key(avatar_key) if avatar_key else None
    db.flush()
    return user
