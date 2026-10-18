"""User lookup, creation and credential verification.

Business logic lives here so the endpoints stay thin and the same rules can be
reused by future features (password reset, admin tooling, ...).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import hash_password, verify_password, verify_password_dummy
from app.db.models import User

# One message for every failed login so the response cannot be used to discover
# which emails are registered.
INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password."


def normalize_email(email: str) -> str:
    """Return the canonical form of an email address.

    Emails are compared and stored lower-cased and trimmed, so
    ``User@Example.com`` and ``user@example.com`` are the same account.
    """
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    """Fetch a user by (normalised) email."""
    statement = select(User).where(User.email == normalize_email(email))
    return db.execute(statement).scalar_one_or_none()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """Fetch a user by primary key."""
    return db.get(User, user_id)


def create_user(db: Session, *, email: str, password: str) -> User:
    """Register a new user.

    Raises :class:`ConflictError` if the email is already taken.
    """
    normalized = normalize_email(email)

    if get_user_by_email(db, normalized) is not None:
        raise ConflictError("An account with this email already exists.")

    user = User(email=normalized, hashed_password=hash_password(password), is_active=True)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        # Covers the race where two requests register the same email at once.
        db.rollback()
        raise ConflictError("An account with this email already exists.") from exc

    return user


def authenticate_user(db: Session, *, email: str, password: str) -> User:
    """Verify credentials and return the user.

    Raises :class:`UnauthorizedError` (identical message) for an unknown email
    or a wrong password, and :class:`ForbiddenError` for a disabled account.
    """
    user = get_user_by_email(db, email)

    if user is None:
        # Spend comparable time so response timing does not reveal existence.
        verify_password_dummy(password)
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE)

    if not verify_password(password, user.hashed_password):
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE)

    if not user.is_active:
        raise ForbiddenError("This account is disabled.")

    return user
