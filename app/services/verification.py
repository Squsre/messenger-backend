"""Single-use tokens for email verification and password reset.

Security properties of a token issued here:

* 256 bits of randomness (:func:`app.core.security.generate_opaque_token`),
* only a SHA-256 digest is stored — never the raw value,
* single use (``used_at``) and short-lived (``expires_at``),
* issuing a new token invalidates any unused token of the same purpose, so an
  older intercepted link cannot be used after a resend.

The raw value is handed to the :class:`~app.services.email.EmailService` in a
link, and is never returned by the API nor written to a normal log.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import BadRequestError
from app.core.security import generate_opaque_token, hash_opaque_token
from app.db.models import User, VerificationPurpose, VerificationToken
from app.services.email import EmailMessage, EmailService

#: One message for every failure mode (unknown, expired, already used) so a
#: caller cannot learn anything about a token they do not hold.
INVALID_TOKEN_MESSAGE = "This link is invalid or has expired."


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _issue_token(
    db: Session,
    *,
    user_id: int,
    purpose: VerificationPurpose,
    ttl_minutes: int,
) -> str:
    """Create a fresh token, invalidating any unused one of the same purpose."""
    now = utcnow()

    outstanding = db.execute(
        select(VerificationToken).where(
            VerificationToken.user_id == user_id,
            VerificationToken.purpose == str(purpose),
            VerificationToken.used_at.is_(None),
        )
    ).scalars().all()
    for stale in outstanding:
        stale.used_at = now

    raw_token = generate_opaque_token()
    db.add(
        VerificationToken(
            user_id=user_id,
            purpose=str(purpose),
            token_hash=hash_opaque_token(raw_token),
            created_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
    )
    db.flush()
    return raw_token


def consume_token(
    db: Session,
    *,
    raw_token: str,
    purpose: VerificationPurpose,
) -> VerificationToken:
    """Validate and burn a token, returning the row it belonged to.

    Raises :class:`BadRequestError` with an identical message for unknown,
    expired and already-used tokens.
    """
    digest = hash_opaque_token(raw_token)
    token = db.execute(
        select(VerificationToken).where(
            VerificationToken.token_hash == digest,
            VerificationToken.purpose == str(purpose),
        )
    ).scalar_one_or_none()

    if token is None or token.is_used:
        raise BadRequestError(INVALID_TOKEN_MESSAGE)

    expires_at = _as_aware(token.expires_at)
    if expires_at is None or expires_at <= utcnow():
        raise BadRequestError(INVALID_TOKEN_MESSAGE)

    token.used_at = utcnow()
    db.flush()
    return token


def prune_expired_tokens(db: Session, *, now: datetime | None = None) -> int:
    """Delete tokens that expired a long time ago (opportunistic cleanup)."""
    moment = now or utcnow()
    result = db.execute(
        delete(VerificationToken).where(VerificationToken.expires_at < moment)
    )
    return result.rowcount or 0


# --- email verification -----------------------------------------------------


def issue_email_verification(
    db: Session,
    *,
    user: User,
    settings: Settings,
    email_service: EmailService,
) -> bool:
    """Send (or resend) the verification link.

    Returns ``False`` without sending anything when the address is already
    verified, so the endpoint can answer identically either way.
    """
    if user.is_email_verified:
        return False

    raw_token = _issue_token(
        db,
        user_id=user.id,
        purpose=VerificationPurpose.EMAIL_VERIFICATION,
        ttl_minutes=settings.email_verification_token_expire_minutes,
    )
    link = f"{settings.public_base_url.rstrip('/')}/verify-email?token={raw_token}"
    email_service.send(
        EmailMessage(
            to=user.email,
            subject="Verify your email address",
            body=(
                f"Hello {user.username},\n\n"
                "Confirm your email address by opening the link below:\n\n"
                f"{link}\n\n"
                f"The link expires in {settings.email_verification_token_expire_minutes} "
                "minutes. If you did not create this account you can ignore this message.\n"
            ),
        )
    )
    return True


def confirm_email_verification(db: Session, *, raw_token: str) -> User:
    """Mark the token owner's email as verified."""
    token = consume_token(
        db,
        raw_token=raw_token,
        purpose=VerificationPurpose.EMAIL_VERIFICATION,
    )
    user = db.get(User, token.user_id)
    if user is None:  # pragma: no cover - FK cascade makes this unreachable
        raise BadRequestError(INVALID_TOKEN_MESSAGE)

    if not user.is_email_verified:
        user.email_verified_at = utcnow()
        db.flush()
    return user


# --- password reset ---------------------------------------------------------


def issue_password_reset(
    db: Session,
    *,
    user: User,
    settings: Settings,
    email_service: EmailService,
) -> str:
    """Send the password-reset link and return the raw token.

    Returning the raw token is for the endpoint to *discard*: it exists so the
    "was a message sent?" decision stays in the caller and the API response is
    identical whether or not the address is registered.
    """
    raw_token = _issue_token(
        db,
        user_id=user.id,
        purpose=VerificationPurpose.PASSWORD_RESET,
        ttl_minutes=settings.password_reset_token_expire_minutes,
    )
    link = f"{settings.public_base_url.rstrip('/')}/reset-password?token={raw_token}"
    email_service.send(
        EmailMessage(
            to=user.email,
            subject="Reset your password",
            body=(
                f"Hello {user.username},\n\n"
                "Use the link below to choose a new password:\n\n"
                f"{link}\n\n"
                f"The link expires in {settings.password_reset_token_expire_minutes} minutes "
                "and can be used once. If you did not request this, no action is needed.\n"
            ),
        )
    )
    return raw_token
