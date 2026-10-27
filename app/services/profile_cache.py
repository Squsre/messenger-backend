"""Safe caching of public profiles.

Only a profile that is already visible to every authenticated user is cached:
``profile_visibility='public'`` **and** ``show_last_seen=true``. Profiles with a
restricted visibility or a hidden last-seen value are never written to Redis,
because those values depend on the viewer or are private.

The cache is invalidated on every profile write (including a username change,
which invalidates both the old and the new key). Blocking is checked against
PostgreSQL on each request, so blocking/unblocking takes effect immediately even
when the profile itself is cached. Entries also have a short TTL as a final
safety net.
"""

from __future__ import annotations

from app.core.security import normalize_username
from app.db.models import ProfileVisibility, User
from app.schemas.user import UserPublic
from app.services.cache import CacheService

PROFILE_CACHE_PREFIX = "profile"


def profile_cache_key(username: str) -> str:
    return f"{PROFILE_CACHE_PREFIX}:{normalize_username(username)}"


def cache_public_profile(
    cache: CacheService,
    *,
    user: User,
    profile: UserPublic,
    ttl_seconds: int,
) -> bool:
    """Cache ``profile`` when it is safe to serve to any authenticated viewer.

    Returns whether a write was attempted successfully. The payload contains
    only fields already present in :class:`UserPublic`; no id, email, account
    state or token is stored in Redis.
    """
    if not user.is_active:
        return False
    if user.profile_visibility != ProfileVisibility.PUBLIC.value:
        return False
    if not user.show_last_seen:
        # The owner may see their own hidden activity; another viewer must not.
        # Keeping one shared entry would either leak it or make the owner's view
        # inconsistent, so these profiles simply are not cached.
        return False

    return cache.set_json(
        profile_cache_key(user.username),
        profile.model_dump(mode="json"),
        ttl_seconds=ttl_seconds,
    )


def get_cached_public_profile(
    cache: CacheService,
    *,
    username: str,
) -> UserPublic | None:
    """Read a cached public profile, discarding stale or unsafe entries."""
    name = profile_cache_key(username)
    payload = cache.get_json(name)
    if not isinstance(payload, dict):
        return None

    if payload.get("profile_visibility") != ProfileVisibility.PUBLIC.value:
        cache.delete(name)
        return None

    try:
        return UserPublic.model_validate(payload)
    except (TypeError, ValueError):
        cache.delete(name)
        return None


def invalidate_public_profile(cache: CacheService, *, username: str) -> None:
    """Drop one username's cached profile after a profile write."""
    cache.delete(profile_cache_key(username))
