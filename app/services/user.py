"""User records: lookup, creation, username derivation and credential checks.

Business logic lives here so the endpoints stay thin and the same rules can be
reused by other features (profile updates, account security, search, admin
tooling, ...).
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import (
    RESERVED_USERNAMES,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    hash_password,
    normalize_username,
    validate_username,
    verify_password,
    verify_password_dummy,
)
from app.db.models import User

# One message for every failed login so the response cannot be used to discover
# which emails are registered.
INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password."

_ILLEGAL_USERNAME_CHARS = re.compile(r"[^a-z0-9_]+")
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")


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


def get_user_by_username(db: Session, username: str) -> User | None:
    """Fetch a user by username (case-insensitive)."""
    statement = select(User).where(User.username == normalize_username(username))
    return db.execute(statement).scalar_one_or_none()


def username_is_taken(db: Session, username: str, *, exclude_user_id: int | None = None) -> bool:
    """Whether an already-normalised username belongs to a *different* account."""
    statement = select(User.id).where(User.username == normalize_username(username))
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    return db.execute(statement).scalar_one_or_none() is not None


def derive_username(db: Session, email: str) -> str:
    """Deterministically derive a free username from an email address.

    Used when an account is created without an explicit username, and by the
    migration that backfills accounts created before usernames existed. The
    result satisfies the username policy and is guaranteed free, so no duplicate
    or reserved name is ever produced.
    """
    local_part = normalize_email(email).split("@", 1)[0]
    base = _ILLEGAL_USERNAME_CHARS.sub("_", local_part)
    base = _REPEATED_UNDERSCORES.sub("_", base).strip("_")

    if len(base) < USERNAME_MIN_LENGTH:
        base = (base + "user")[:USERNAME_MAX_LENGTH]
    base = base[:USERNAME_MAX_LENGTH]

    if base in RESERVED_USERNAMES:
        base = f"{base}_1"[:USERNAME_MAX_LENGTH]

    candidate = base
    suffix = 2
    while username_is_taken(db, candidate):
        tail = f"_{suffix}"
        candidate = f"{base[: USERNAME_MAX_LENGTH - len(tail)]}{tail}"
        suffix += 1
    return candidate


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    username: str | None = None,
) -> User:
    """Register a new user.

    ``username`` is optional for backwards compatibility; when it is omitted a
    deterministic one is derived from the email and can be changed later through
    the profile API.

    Raises :class:`ConflictError` if the email (or an explicitly supplied
    username) is already taken.
    """
    normalized_email = normalize_email(email)

    if get_user_by_email(db, normalized_email) is not None:
        raise ConflictError("An account with this email already exists.")

    if username is None:
        chosen_username = derive_username(db, normalized_email)
    else:
        chosen_username = validate_username(username)
        if username_is_taken(db, chosen_username):
            raise ConflictError("That username is already taken.")

    user = User(
        email=normalized_email,
        username=chosen_username,
        hashed_password=hash_password(password),
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        # Covers the race where two requests take the same email/username at once.
        db.rollback()
        raise ConflictError("An account with this email or username already exists.") from exc

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


def set_password(db: Session, user: User, new_password: str) -> None:
    """Replace the password hash and invalidate every previously issued token.

    Bumping ``token_version`` is what makes password change/reset actually end
    other sessions: any token minted with the old version is rejected.
    """
    user.hashed_password = hash_password(new_password)
    user.token_version += 1
    db.flush()


def email_exists(db: Session, email: str) -> bool:
    """Whether an account with this (normalised) email exists."""
    return get_user_by_email(db, email) is not None


def count_users(db: Session) -> int:
    """Total number of accounts (used by tests and diagnostics)."""
    return int(db.execute(select(func.count()).select_from(User)).scalar_one())
