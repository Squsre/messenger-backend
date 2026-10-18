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


def revoke_token(db: Session, claims: AccessTokenClaims, *, user_id: int | None = None) -> None:
    """Add a token to the denylist.

    Idempotent: revoking the same token twice is not an error.
    """
    db.add(
        TokenBlocklist(
            jti=claims.jti,
            user_id=user_id,
            token_type=claims.token_type,
            expires_at=claims.expires_at,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        # Already revoked (or a concurrent logout); that is fine.
        db.rollback()


def prune_expired_tokens(db: Session, *, now: datetime | None = None) -> int:
    """Delete denylist rows whose token has expired anyway.

    Returns the number of rows removed. Called opportunistically so the table
    does not grow without bound.
    """
    moment = now or datetime.now(timezone.utc)
    result = db.execute(delete(TokenBlocklist).where(TokenBlocklist.expires_at < moment))
    return result.rowcount or 0
