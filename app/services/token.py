"""Access-token revocation (logout) backed by the database.

See :mod:`app.db.models.token_blocklist` for the why. These helpers keep the
endpoint and the dependencies free of query details.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import AccessTokenClaims
from app.db.models import TokenBlocklist


def is_token_revoked(db: Session, jti: str) -> bool:
    """Return ``True`` if the token id has been revoked."""
    statement = select(TokenBlocklist.id).where(TokenBlocklist.jti == jti)
    return db.execute(statement).scalar_one_or_none() is not None


def blocklist_token_id(
    db: Session,
    *,
    jti: str,
    expires_at: datetime,
    user_id: int | None = None,
    token_type: str = "access",
) -> None:
    """Add a token id to the denylist.

    Idempotent: revoking the same token twice is not an error. Used directly by
    "log out everywhere"/single-session revocation, which know the ``jti`` of
    each session but no longer hold its claims object.

    The insert runs inside a ``SAVEPOINT`` and the row is added *after* it opens,
    so a lost race (another request inserted the same ``jti`` first) rolls back
    only this insert and leaves the caller's pending changes — such as a
    ``token_version`` bump — intact.
    """
    if is_token_revoked(db, jti):
        return

    try:
        with db.begin_nested():
            db.add(
                TokenBlocklist(
                    jti=jti,
                    user_id=user_id,
                    token_type=token_type,
                    expires_at=expires_at,
                )
            )
            db.flush()
    except IntegrityError:
        # Already revoked (or a concurrent logout); that is fine.
        pass


def revoke_token(db: Session, claims: AccessTokenClaims, *, user_id: int | None = None) -> None:
    """Add a verified token's id to the denylist.

    Idempotent: revoking the same token twice is not an error.
    """
    blocklist_token_id(
        db,
        jti=claims.jti,
        expires_at=claims.expires_at,
        user_id=user_id,
        token_type=claims.token_type,
    )


def prune_expired_tokens(db: Session, *, now: datetime | None = None) -> int:
    """Delete denylist rows whose token has expired anyway.

    Returns the number of rows removed. Called opportunistically so the table
    does not grow without bound.
    """
    moment = now or datetime.now(timezone.utc)
    result = db.execute(delete(TokenBlocklist).where(TokenBlocklist.expires_at < moment))
    return result.rowcount or 0
