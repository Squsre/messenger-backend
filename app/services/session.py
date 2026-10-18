"""Device sessions.

A row is written on every successful login and is identified by the **same
random id as the access token's ``jti``** — the token itself (or any secret) is
never stored, so a database leak cannot be replayed. That single id is enough to
list and revoke a device.

Revocation of one session is enforced through the existing token denylist; the
session row additionally records that the device is finished so it disappears
from the user's list. "Log out everywhere" bumps ``User.token_version``, which
invalidates *every* previously issued token at once — including tokens minted
before sessions existed, so the guarantee covers legacy tokens too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, UserSession
from app.db.models.user_session import USER_AGENT_MAX_LENGTH
from app.services.token import blocklist_token_id

#: Characters that must never reach a stored user-agent header.
_CONTROL_CHARACTERS = dict.fromkeys(range(0x20), None)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_user_agent(value: str | None) -> str | None:
    """Keep a bounded, printable prefix of a User-Agent header.

    The header is attacker-controlled, so it is stripped of control characters
    and truncated before it is stored or echoed back.
    """
    if not value:
        return None
    cleaned = value.translate(_CONTROL_CHARACTERS).strip()
    if not cleaned:
        return None
    return cleaned[:USER_AGENT_MAX_LENGTH]


def record_session(
    db: Session,
    *,
    user: User,
    session_id: str,
    expires_at: datetime,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> UserSession:
    """Persist a newly logged-in device."""
    now = utcnow()
    session = UserSession(
        session_id=session_id,
        user_id=user.id,
        user_agent=sanitize_user_agent(user_agent),
        ip_address=ip_address,
        created_at=now,
        last_active_at=now,
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()
    return session


def get_session(db: Session, *, session_id: str) -> UserSession | None:
    """Look a session up by the access token's ``jti``."""
    statement = select(UserSession).where(UserSession.session_id == session_id)
    return db.execute(statement).scalar_one_or_none()


def get_user_session(db: Session, *, user_id: int, session_pk: int) -> UserSession | None:
    """Look a session up by id **within one user's own devices**.

    Scoping the query by ``user_id`` is what makes another user's session
    impossible to enumerate or revoke: it simply does not exist for this caller.
    """
    statement = select(UserSession).where(
        UserSession.id == session_pk,
        UserSession.user_id == user_id,
    )
    return db.execute(statement).scalar_one_or_none()


def list_active_sessions(db: Session, *, user_id: int, now: datetime | None = None) -> list[UserSession]:
    """Return the user's usable devices, most recently active first."""
    moment = now or utcnow()
    statement = (
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > moment,
        )
        .order_by(UserSession.last_active_at.desc(), UserSession.id.desc())
    )
    return list(db.execute(statement).scalars().all())


def touch_session(db: Session, session: UserSession, *, interval_seconds: int) -> bool:
    """Refresh ``last_active_at`` at most once per ``interval_seconds``."""
    now = utcnow()
    if interval_seconds > 0 and session.last_active_at is not None:
        previous = session.last_active_at
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        if now - previous < timedelta(seconds=interval_seconds):
            return False
    session.last_active_at = now
    db.flush()
    return True


def revoke_session(db: Session, *, session: UserSession) -> None:
    """End a single device: denylist its token and mark the row revoked."""
    blocklist_token_id(
        db,
        jti=session.session_id,
        expires_at=session.expires_at,
        user_id=session.user_id,
    )
    session.revoked_at = utcnow()
    db.flush()


def revoke_all_sessions(db: Session, *, user: User, bump_token_version: bool = True) -> int:
    """Invalidate every token the user holds and end all their devices.

    Returns the number of session rows that were closed. Bumping
    ``token_version`` is what makes the guarantee complete: tokens issued before
    sessions existed carry version ``0`` and stop verifying as well.

    ``bump_token_version=False`` is used by the password flows, where
    :func:`app.services.user.set_password` has already advanced the version; the
    sessions still have to be closed and denylisted.
    """
    if bump_token_version:
        user.token_version += 1

    sessions = db.execute(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
    ).scalars().all()

    now = utcnow()
    for session in sessions:
        blocklist_token_id(
            db,
            jti=session.session_id,
            expires_at=session.expires_at,
            user_id=user.id,
        )
        session.revoked_at = now
    db.flush()
    return len(sessions)


def prune_finished_sessions(db: Session, *, user_id: int) -> int:
    """Delete long-finished session rows this user no longer needs.

    Only rows that are both revoked and past their expiry are removed, so the
    device list cannot grow without bound while fresh records are kept.
    """
    now = utcnow()
    statement = select(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_not(None),
        UserSession.expires_at < now,
    )
    rows = db.execute(statement).scalars().all()
    for row in rows:
        db.delete(row)
    if rows:
        db.flush()
    return len(rows)
