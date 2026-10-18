"""Contacts, blocks and the user search built on top of them.

Relationship model (documented in ``README.md``): a contact relationship is
**one-directional**. Adding ``bob`` to ``alice``'s contacts creates a single row
``(owner=alice, contact=bob)`` and does *not* make ``alice`` a contact of ``bob``.
Mutual "connection requests" are a documented later sub-phase, so no fake
request/accept state machine is shipped here.

Blocks are stored directionally but applied symmetrically: if either side has
blocked the other, neither can see the other's profile or interact.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.db.models import Block, Contact, ProfileVisibility, User

#: Sentinel above every allowed username character, used to turn a prefix match
#: into an index-friendly range scan (``LIKE 'abc%'`` cannot use a plain b-tree
#: index on PostgreSQL; a range can).
_PREFIX_UPPER_BOUND = chr(0x10FFFF)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- contacts ---------------------------------------------------------------


def is_contact(db: Session, *, owner_id: int, contact_id: int) -> bool:
    """Whether ``owner`` has ``contact`` in their contact list."""
    statement = select(Contact.id).where(
        Contact.owner_id == owner_id,
        Contact.contact_id == contact_id,
    )
    return db.execute(statement).scalar_one_or_none() is not None


def get_contact(db: Session, *, owner_id: int, contact_id: int) -> Contact | None:
    statement = select(Contact).where(
        Contact.owner_id == owner_id,
        Contact.contact_id == contact_id,
    )
    return db.execute(statement).scalar_one_or_none()


def add_contact(db: Session, *, owner: User, target: User) -> Contact:
    """Add ``target`` to ``owner``'s contacts.

    Rejects self-relationships, duplicates and any interaction across a block.
    """
    if owner.id == target.id:
        raise BadRequestError("You cannot add yourself as a contact.")
    if not target.is_active:
        # Same response as an unknown username: inactive accounts are invisible.
        raise NotFoundError("User not found.")
    if is_blocked_between(db, owner.id, target.id):
        raise ForbiddenError("This user is not available.")
    if is_contact(db, owner_id=owner.id, contact_id=target.id):
        raise ConflictError("This user is already in your contacts.")

    contact = Contact(owner_id=owner.id, contact_id=target.id)
    db.add(contact)
    try:
        db.flush()
    except IntegrityError as exc:  # concurrent insert of the same pair
        db.rollback()
        raise ConflictError("This user is already in your contacts.") from exc
    return contact


def remove_contact(db: Session, *, owner_id: int, contact_id: int) -> bool:
    """Delete the relationship. Returns whether a row was removed."""
    contact = get_contact(db, owner_id=owner_id, contact_id=contact_id)
    if contact is None:
        return False
    db.delete(contact)
    db.flush()
    return True


def _contacts_query(owner_id: int) -> Select[tuple[User]]:
    """Contacts visible in the owner's list.

    Users on either side of a block are hidden but **not** deleted, so unblocking
    restores the previous list instead of silently losing data.
    """
    blocked_by_owner = select(Block.blocked_id).where(Block.blocker_id == owner_id)
    blocked_owner = select(Block.blocker_id).where(Block.blocked_id == owner_id)
    return (
        select(User)
        .join(Contact, Contact.contact_id == User.id)
        .where(
            Contact.owner_id == owner_id,
            ~User.id.in_(blocked_by_owner),
            ~User.id.in_(blocked_owner),
        )
    )


def list_contacts(db: Session, *, owner_id: int, limit: int, offset: int) -> list[User]:
    """Page through a user's contacts, newest addition first."""
    statement = (
        _contacts_query(owner_id)
        .order_by(Contact.created_at.desc(), User.username.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_contacts(db: Session, *, owner_id: int) -> int:
    subquery = _contacts_query(owner_id).subquery()
    statement = select(func.count()).select_from(subquery)
    return int(db.execute(statement).scalar_one())


# --- blocks -----------------------------------------------------------------


def has_blocked(db: Session, *, blocker_id: int, blocked_id: int) -> bool:
    statement = select(Block.id).where(
        Block.blocker_id == blocker_id,
        Block.blocked_id == blocked_id,
    )
    return db.execute(statement).scalar_one_or_none() is not None


def is_blocked_between(db: Session, first_id: int, second_id: int) -> bool:
    """Whether a block exists in either direction between two users."""
    statement = select(Block.id).where(
        Block.blocker_id.in_((first_id, second_id)),
        Block.blocked_id.in_((first_id, second_id)),
    )
    return db.execute(statement).first() is not None


def block_user(db: Session, *, blocker: User, blocked: User) -> Block:
    """Block ``blocked`` on behalf of ``blocker``."""
    if blocker.id == blocked.id:
        raise BadRequestError("You cannot block yourself.")
    if not blocked.is_active:
        raise NotFoundError("User not found.")
    if has_blocked(db, blocker_id=blocker.id, blocked_id=blocked.id):
        raise ConflictError("This user is already blocked.")

    block = Block(blocker_id=blocker.id, blocked_id=blocked.id)
    db.add(block)
    try:
        db.flush()
    except IntegrityError as exc:  # concurrent insert of the same pair
        db.rollback()
        raise ConflictError("This user is already blocked.") from exc
    return block


def unblock_user(db: Session, *, blocker_id: int, blocked_id: int) -> bool:
    """Remove a block. Returns whether one existed.

    Blocking does not delete the contact relationship that may already exist, so
    unblocking restores the previous state instead of silently dropping data.
    """
    statement = select(Block).where(
        Block.blocker_id == blocker_id,
        Block.blocked_id == blocked_id,
    )
    block = db.execute(statement).scalar_one_or_none()
    if block is None:
        return False
    db.delete(block)
    db.flush()
    return True


def list_blocked(db: Session, *, blocker_id: int, limit: int, offset: int) -> list[User]:
    statement = (
        select(User)
        .join(Block, Block.blocked_id == User.id)
        .where(Block.blocker_id == blocker_id)
        .order_by(Block.created_at.desc(), User.username.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_blocked(db: Session, *, blocker_id: int) -> int:
    statement = select(func.count()).select_from(Block).where(Block.blocker_id == blocker_id)
    return int(db.execute(statement).scalar_one())


# --- search -----------------------------------------------------------------


def _search_conditions(query: str, *, viewer_id: int) -> list:
    """Filters shared by the search page and its count.

    Excludes the viewer, deactivated accounts, users who opted out of discovery
    (``profile_visibility = private``), and anyone on either side of a block.
    The username filter is a range scan so the unique index on ``users.username``
    is usable on SQLite *and* PostgreSQL.
    """
    blocked_by_viewer = select(Block.blocked_id).where(Block.blocker_id == viewer_id)
    blocked_viewer = select(Block.blocker_id).where(Block.blocked_id == viewer_id)

    return [
        User.is_active.is_(True),
        User.profile_visibility != ProfileVisibility.PRIVATE.value,
        User.id != viewer_id,
        ~User.id.in_(blocked_by_viewer),
        ~User.id.in_(blocked_viewer),
        User.username >= query,
        User.username < query + _PREFIX_UPPER_BOUND,
    ]


def search_users(
    db: Session,
    *,
    query: str,
    viewer_id: int,
    limit: int,
    offset: int,
) -> list[User]:
    """Find users whose username starts with ``query`` (exact match ranked first)."""
    statement = (
        select(User)
        .where(*_search_conditions(query, viewer_id=viewer_id))
        .order_by(
            # Exact matches first, then the rest alphabetically.
            func.length(User.username) != len(query),
            User.username.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_search_results(db: Session, *, query: str, viewer_id: int) -> int:
    statement = select(func.count()).select_from(User).where(
        *_search_conditions(query, viewer_id=viewer_id)
    )
    return int(db.execute(statement).scalar_one())
