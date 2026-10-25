"""Business rules for private and public groups.

Groups are an extension of the existing conversation model, not a second chat
system:

* the conversation row holds group metadata and points at its owner,
* ``conversation_members`` holds membership and the member's role,
* the existing ``messages`` table stores group messages,
* ``message_receipts`` stores delivery/read state per member.

Every authorization decision is derived from the database. A request can name a
group id, user id or role, but none of those values are trusted: the service
loads the caller's membership row and compares roles on the server. A
non-member sees a private group (and its members/messages) as ``404``; a public
group's profile is visible, but its messages still require membership.

Role rules
----------
* Owner: can edit all fields, invite, remove admins/members, change roles,
  transfer ownership, and cannot leave or be removed while owning the group.
* Admin: can edit name/description, invite, and remove ordinary members. An
  admin cannot remove another admin, the owner, or change roles.
* Member: can read/send messages, view the member list, leave, and invite only
  if the service is explicitly called by a manager (endpoints enforce this).

Invitations are pending until accepted, declined or revoked. A partial unique
index prevents two pending invitations for the same invitee. Blocks are enforced
when inviting: a blocked pair cannot be brought together through a group
invitation. Existing group members are not silently removed when a block is
created; group messages remain multi-party and are not suppressed pairwise.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.db.models import (
    Conversation,
    ConversationKind,
    ConversationMember,
    GroupInvitation,
    GroupRole,
    GroupVisibility,
    InvitationStatus,
    Message,
    MessageReceipt,
    User,
)
from app.services import chat as chat_service
from app.services.contact import is_blocked_between

_GROUP = ConversationKind.GROUP.value
_DIRECT = ConversationKind.DIRECT.value
_OWNER = GroupRole.OWNER.value
_ADMIN = GroupRole.ADMIN.value
_MEMBER = GroupRole.MEMBER.value
_PENDING = InvitationStatus.PENDING.value
_ACCEPTED = InvitationStatus.ACCEPTED.value
_DECLINED = InvitationStatus.DECLINED.value
_REVOKED = InvitationStatus.REVOKED.value

_ROLE_RANK = {_OWNER: 0, _ADMIN: 1, _MEMBER: 2}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_group_pair_key() -> str:
    """A unique, non-numeric key for a group conversation.

    The prefix cannot occur in a direct pair key (``"<id>:<id>"``), so group and
    direct rows can never collide even though both live in ``conversations``.
    """
    return f"group:{secrets.token_hex(16)}"


def is_group(conversation: Conversation) -> bool:
    return conversation.kind == _GROUP


def get_group(db: Session, group_id: int) -> Conversation | None:
    conversation = db.get(Conversation, group_id)
    if conversation is None or conversation.kind != _GROUP:
        return None
    return conversation


def require_group(db: Session, group_id: int) -> Conversation:
    group = get_group(db, group_id)
    if group is None:
        raise NotFoundError("Group not found.")
    return group


def get_membership(
    db: Session, *, group_id: int, user_id: int
) -> ConversationMember | None:
    return db.execute(
        select(ConversationMember).where(
            ConversationMember.conversation_id == group_id,
            ConversationMember.user_id == user_id,
        )
    ).scalar_one_or_none()


def require_member(
    db: Session, *, group_id: int, user_id: int
) -> tuple[Conversation, ConversationMember]:
    """Return the group and the caller's membership, or ``404``."""
    group = get_group(db, group_id)
    if group is None:
        raise NotFoundError("Group not found.")
    member = get_membership(db, group_id=group_id, user_id=user_id)
    if member is None:
        raise NotFoundError("Group not found.")
    return group, member


def require_manager(
    db: Session, *, group_id: int, user_id: int
) -> tuple[Conversation, ConversationMember]:
    group, member = require_member(db, group_id=group_id, user_id=user_id)
    if member.role not in {_OWNER, _ADMIN}:
        raise ForbiddenError("Only the group owner or an admin can do this.")
    return group, member


def require_owner(
    db: Session, *, group_id: int, user_id: int
) -> tuple[Conversation, ConversationMember]:
    group, member = require_member(db, group_id=group_id, user_id=user_id)
    if member.role != _OWNER:
        raise ForbiddenError("Only the group owner can do this.")
    return group, member


def member_ids(db: Session, *, group_id: int) -> list[int]:
    return list(
        db.execute(
            select(ConversationMember.user_id).where(
                ConversationMember.conversation_id == group_id
            )
        ).scalars()
    )


def member_count(db: Session, *, group_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(ConversationMember)
            .where(ConversationMember.conversation_id == group_id)
        ).scalar_one()
    )


def member_counts(db: Session, *, group_ids: list[int]) -> dict[int, int]:
    if not group_ids:
        return {}
    statement = (
        select(ConversationMember.conversation_id, func.count())
        .where(ConversationMember.conversation_id.in_(group_ids))
        .group_by(ConversationMember.conversation_id)
    )
    return {
        int(group_id): int(total)
        for group_id, total in db.execute(statement).all()
    }


def roles_for(
    db: Session, *, user_id: int, group_ids: list[int]
) -> dict[int, str]:
    if not group_ids:
        return {}
    statement = select(
        ConversationMember.conversation_id, ConversationMember.role
    ).where(
        ConversationMember.user_id == user_id,
        ConversationMember.conversation_id.in_(group_ids),
    )
    return {
        int(group_id): str(role)
        for group_id, role in db.execute(statement).all()
    }


def usernames_for(db: Session, *, user_ids: list[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    statement = select(User.id, User.username).where(User.id.in_(user_ids))
    return {int(user_id): str(username) for user_id, username in db.execute(statement).all()}


# --- creation and discovery -------------------------------------------------


def create_group(
    db: Session,
    *,
    owner: User,
    name: str,
    description: str | None,
    visibility: str,
) -> Conversation:
    """Create a group and the owner's membership atomically."""
    with db.begin_nested():
        group = Conversation(
            pair_key=new_group_pair_key(),
            kind=_GROUP,
            name=name,
            description=description,
            visibility=visibility,
            owner_id=owner.id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(group)
        db.flush()
        db.add(
            ConversationMember(
                conversation_id=group.id,
                user_id=owner.id,
                role=_OWNER,
                created_at=utcnow(),
            )
        )
        db.flush()
    return group


def update_group(
    db: Session,
    *,
    group: Conversation,
    actor_id: int,
    changes: dict[str, object],
) -> Conversation:
    """Apply an owner/admin edit, enforcing role boundaries."""
    _, member = require_manager(db, group_id=group.id, user_id=actor_id)
    if "visibility" in changes and member.role != _OWNER:
        raise ForbiddenError("Only the group owner can change group visibility.")

    if "name" in changes:
        group.name = str(changes["name"])
    if "description" in changes:
        value = changes["description"]
        group.description = None if value is None else str(value)
    if "visibility" in changes:
        group.visibility = str(changes["visibility"])
    group.updated_at = utcnow()
    db.flush()
    return group


def _user_group_query(user_id: int) -> Select[tuple[Conversation]]:
    return (
        select(Conversation)
        .join(
            ConversationMember,
            ConversationMember.conversation_id == Conversation.id,
        )
        .where(
            Conversation.kind == _GROUP,
            ConversationMember.user_id == user_id,
        )
    )


def list_user_groups(
    db: Session, *, user_id: int, limit: int, offset: int
) -> list[tuple[Conversation, str]]:
    """Page through groups the caller belongs to, most recent first."""
    statement = (
        select(Conversation, ConversationMember.role)
        .join(
            ConversationMember,
            ConversationMember.conversation_id == Conversation.id,
        )
        .where(
            Conversation.kind == _GROUP,
            ConversationMember.user_id == user_id,
        )
        .order_by(
            func.coalesce(
                Conversation.last_message_at, Conversation.created_at
            ).desc(),
            Conversation.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return [
        (conversation, str(role))
        for conversation, role in db.execute(statement).all()
    ]


def count_user_groups(db: Session, *, user_id: int) -> int:
    subquery = _user_group_query(user_id).subquery()
    return int(
        db.execute(select(func.count()).select_from(subquery)).scalar_one()
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _public_search_conditions(query: str) -> list:
    pattern = f"%{_escape_like(query.lower())}%"
    return [
        Conversation.kind == _GROUP,
        Conversation.visibility == GroupVisibility.PUBLIC.value,
        or_(
            func.lower(Conversation.name).like(pattern, escape="\\"),
            func.lower(Conversation.description).like(pattern, escape="\\"),
        ),
    ]


def search_public_groups(
    db: Session, *, query: str, limit: int, offset: int
) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(*_public_search_conditions(query))
        .order_by(
            func.coalesce(
                Conversation.last_message_at, Conversation.created_at
            ).desc(),
            Conversation.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_public_groups(db: Session, *, query: str) -> int:
    subquery = (
        select(Conversation.id).where(*_public_search_conditions(query)).subquery()
    )
    return int(
        db.execute(select(func.count()).select_from(subquery)).scalar_one()
    )


def can_view_group(group: Conversation, *, role: str | None) -> bool:
    """Private groups are invisible to non-members; public profiles are open."""
    return role is not None or group.visibility == GroupVisibility.PUBLIC.value


# --- members ----------------------------------------------------------------


def list_members(
    db: Session, *, group_id: int, limit: int, offset: int
) -> list[tuple[ConversationMember, User]]:
    role_order = case(
        (ConversationMember.role == _OWNER, 0),
        (ConversationMember.role == _ADMIN, 1),
        else_=2,
    )
    statement = (
        select(ConversationMember, User)
        .join(User, User.id == ConversationMember.user_id)
        .where(ConversationMember.conversation_id == group_id)
        .order_by(
            role_order,
            ConversationMember.created_at.asc(),
            ConversationMember.user_id.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).all())


def join_public_group(
    db: Session, *, group: Conversation, user_id: int
) -> tuple[ConversationMember, bool]:
    """Join a public group; a repeated join returns the existing membership."""
    if group.visibility != GroupVisibility.PUBLIC.value:
        raise ForbiddenError("This group is private; a valid invitation is required.")
    existing = get_membership(db, group_id=group.id, user_id=user_id)
    if existing is not None:
        return existing, False
    if group.owner_id is not None and is_blocked_between(
        db, user_id, group.owner_id
    ):
        raise ForbiddenError("This group is not available.")

    try:
        with db.begin_nested():
            member = ConversationMember(
                conversation_id=group.id,
                user_id=user_id,
                role=_MEMBER,
                created_at=utcnow(),
            )
            db.add(member)
            db.flush()
    except IntegrityError:
        # A concurrent join won; use its row instead of failing the request.
        existing = get_membership(db, group_id=group.id, user_id=user_id)
        if existing is None:  # pragma: no cover - only a broken constraint path
            raise
        return existing, False
    return member, True


def remove_member(
    db: Session, *, group: Conversation, actor_id: int, target_id: int
) -> tuple[ConversationMember, User]:
    """Remove a member under the owner/admin rules."""
    _, actor = require_manager(db, group_id=group.id, user_id=actor_id)
    target_member = get_membership(db, group_id=group.id, user_id=target_id)
    if target_member is None:
        raise NotFoundError("Group member not found.")
    if target_member.role == _OWNER:
        raise ForbiddenError("The group owner cannot be removed.")
    if actor.role == _ADMIN and target_member.role == _ADMIN:
        raise ForbiddenError("An admin cannot remove another admin.")
    target_user = db.get(User, target_id)
    if target_user is None:  # pragma: no cover - FK keeps this consistent
        raise NotFoundError("Group member not found.")
    db.delete(target_member)
    db.flush()
    return target_member, target_user


def leave_group(
    db: Session, *, group: Conversation, user_id: int
) -> ConversationMember:
    _, member = require_member(db, group_id=group.id, user_id=user_id)
    if member.role == _OWNER:
        raise ConflictError(
            "The group owner cannot leave without transferring ownership first."
        )
    db.delete(member)
    db.flush()
    return member


def change_role(
    db: Session,
    *,
    group: Conversation,
    actor_id: int,
    target_id: int,
    role: str,
) -> tuple[ConversationMember, User]:
    """Promote/demote a member; only the owner may do this."""
    _, actor = require_owner(db, group_id=group.id, user_id=actor_id)
    if role not in {_ADMIN, _MEMBER}:
        raise BadRequestError("Role must be 'admin' or 'member'.")
    target_member = get_membership(db, group_id=group.id, user_id=target_id)
    if target_member is None:
        raise NotFoundError("Group member not found.")
    if target_member.role == _OWNER:
        raise ConflictError("The owner's role cannot be changed here.")
    if actor.user_id == target_member.user_id:
        raise ConflictError("You cannot change your own role.")
    target_user = db.get(User, target_id)
    if target_user is None:  # pragma: no cover - FK keeps this consistent
        raise NotFoundError("Group member not found.")
    target_member.role = role
    db.flush()
    return target_member, target_user


def transfer_ownership(
    db: Session, *, group: Conversation, actor_id: int, target_id: int
) -> tuple[ConversationMember, ConversationMember, User]:
    """Transfer ownership to a current member and demote the old owner."""
    _, actor = require_owner(db, group_id=group.id, user_id=actor_id)
    target_member = get_membership(db, group_id=group.id, user_id=target_id)
    if target_member is None:
        raise NotFoundError("Group member not found.")
    if target_member.user_id == actor.user_id:
        raise BadRequestError("Ownership is already yours.")
    target_user = db.get(User, target_id)
    if target_user is None:  # pragma: no cover - FK keeps this consistent
        raise NotFoundError("Group member not found.")

    target_member.role = _OWNER
    actor.role = _ADMIN
    group.owner_id = target_id
    db.flush()
    return target_member, actor, target_user


# --- invitations ------------------------------------------------------------


def get_invitation(
    db: Session, *, group_id: int, invitation_id: int
) -> GroupInvitation | None:
    invitation = db.get(GroupInvitation, invitation_id)
    if invitation is None or invitation.conversation_id != group_id:
        return None
    return invitation


def create_invitation(
    db: Session,
    *,
    group: Conversation,
    inviter_id: int,
    target: User,
) -> GroupInvitation:
    """Invite an active account, unless it is already a member or blocked."""
    require_manager(db, group_id=group.id, user_id=inviter_id)
    if inviter_id == target.id:
        raise BadRequestError("You cannot invite yourself.")
    if not target.is_active:
        raise NotFoundError("User not found.")
    if get_membership(db, group_id=group.id, user_id=target.id) is not None:
        raise ConflictError("This user is already a member of the group.")
    if is_blocked_between(db, inviter_id, target.id):
        raise ForbiddenError("This user is not available.")

    existing = db.execute(
        select(GroupInvitation).where(
            GroupInvitation.conversation_id == group.id,
            GroupInvitation.invitee_id == target.id,
            GroupInvitation.status == _PENDING,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("An invitation is already pending for this user.")

    try:
        with db.begin_nested():
            invitation = GroupInvitation(
                conversation_id=group.id,
                inviter_id=inviter_id,
                invitee_id=target.id,
                status=_PENDING,
                created_at=utcnow(),
            )
            db.add(invitation)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "An invitation is already pending for this user."
        ) from exc
    return invitation


def require_invitation_for_invitee(
    db: Session, *, group_id: int, invitation_id: int, user_id: int
) -> GroupInvitation:
    invitation = get_invitation(db, group_id=group_id, invitation_id=invitation_id)
    if invitation is None or invitation.invitee_id != user_id:
        # Do not confirm that another account's invitation exists.
        raise NotFoundError("Invitation not found.")
    return invitation


def accept_invitation(
    db: Session, *, group: Conversation, invitation: GroupInvitation
) -> tuple[ConversationMember, bool]:
    """Accept a pending invitation and add the invitee to the group."""
    if invitation.status == _ACCEPTED:
        existing = get_membership(
            db, group_id=group.id, user_id=invitation.invitee_id
        )
        if existing is not None:
            return existing, False
    if invitation.status != _PENDING:
        raise ConflictError("This invitation is no longer pending.")
    if is_blocked_between(db, invitation.inviter_id, invitation.invitee_id):
        raise ForbiddenError("This invitation is no longer available.")

    existing = get_membership(
        db, group_id=group.id, user_id=invitation.invitee_id
    )
    if existing is not None:
        invitation.status = _ACCEPTED
        invitation.responded_at = utcnow()
        db.flush()
        return existing, False

    created = True
    try:
        with db.begin_nested():
            member = ConversationMember(
                conversation_id=group.id,
                user_id=invitation.invitee_id,
                role=_MEMBER,
                created_at=utcnow(),
            )
            db.add(member)
            db.flush()
    except IntegrityError:
        existing = get_membership(
            db, group_id=group.id, user_id=invitation.invitee_id
        )
        if existing is None:  # pragma: no cover - only a broken constraint path
            raise
        member = existing
        created = False

    invitation.status = _ACCEPTED
    invitation.responded_at = utcnow()
    db.flush()
    return member, created


def decline_invitation(invitation: GroupInvitation) -> None:
    if invitation.status != _PENDING:
        raise ConflictError("This invitation is no longer pending.")
    invitation.status = _DECLINED
    invitation.responded_at = utcnow()


def revoke_invitation(
    db: Session, *, group: Conversation, actor_id: int, invitation: GroupInvitation
) -> None:
    require_manager(db, group_id=group.id, user_id=actor_id)
    if invitation.status != _PENDING:
        raise ConflictError("Only a pending invitation can be revoked.")
    invitation.status = _REVOKED
    invitation.responded_at = utcnow()
    db.flush()


def list_group_invitations(
    db: Session,
    *,
    group_id: int,
    status: str | None,
    limit: int,
    offset: int,
) -> list[GroupInvitation]:
    statement = select(GroupInvitation).where(
        GroupInvitation.conversation_id == group_id
    )
    if status is not None:
        statement = statement.where(GroupInvitation.status == status)
    statement = (
        statement.order_by(GroupInvitation.created_at.desc(), GroupInvitation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_group_invitations(
    db: Session, *, group_id: int, status: str | None
) -> int:
    statement = (
        select(func.count())
        .select_from(GroupInvitation)
        .where(GroupInvitation.conversation_id == group_id)
    )
    if status is not None:
        statement = statement.where(GroupInvitation.status == status)
    return int(db.execute(statement).scalar_one())


def list_my_invitations(
    db: Session,
    *,
    user_id: int,
    status: str | None,
    limit: int,
    offset: int,
) -> list[GroupInvitation]:
    statement = select(GroupInvitation).where(
        GroupInvitation.invitee_id == user_id
    )
    if status is not None:
        statement = statement.where(GroupInvitation.status == status)
    statement = (
        statement.order_by(GroupInvitation.created_at.desc(), GroupInvitation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars().all())


def count_my_invitations(
    db: Session, *, user_id: int, status: str | None
) -> int:
    statement = (
        select(func.count())
        .select_from(GroupInvitation)
        .where(GroupInvitation.invitee_id == user_id)
    )
    if status is not None:
        statement = statement.where(GroupInvitation.status == status)
    return int(db.execute(statement).scalar_one())


# --- group messages and per-member receipts ---------------------------------


@dataclass(frozen=True)
class GroupMessageStats:
    read_count: int
    delivered_count: int
    member_count: int
    read_by_me: bool
    delivered_to_me: bool


@dataclass(frozen=True)
class GroupReadState:
    last_read_message_id: int
    read_at: datetime | None
    updated: int
    unread_count: int


def _receipts_for_messages(
    db: Session, *, message_ids: list[int]
) -> dict[int, list[MessageReceipt]]:
    if not message_ids:
        return {}
    statement = select(MessageReceipt).where(
        MessageReceipt.message_id.in_(message_ids)
    )
    grouped: dict[int, list[MessageReceipt]] = {}
    for receipt in db.execute(statement).scalars().all():
        grouped.setdefault(receipt.message_id, []).append(receipt)
    return grouped


def message_stats(
    db: Session,
    *,
    messages: list[Message],
    viewer_id: int,
    member_count_value: int,
) -> dict[int, GroupMessageStats]:
    """Bulk receipt aggregates for a page of messages (one receipt query)."""
    receipts = _receipts_for_messages(
        db, message_ids=[message.id for message in messages]
    )
    stats: dict[int, GroupMessageStats] = {}
    for message in messages:
        own = message.sender_id == viewer_id
        message_receipts = [
            receipt
            for receipt in receipts.get(message.id, [])
            if receipt.user_id != message.sender_id
        ]
        read_count = sum(1 for receipt in message_receipts if receipt.read_at)
        delivered_count = sum(
            1 for receipt in message_receipts if receipt.delivered_at
        )
        viewer_receipt = next(
            (
                receipt
                for receipt in message_receipts
                if receipt.user_id == viewer_id
            ),
            None,
        )
        stats[message.id] = GroupMessageStats(
            read_count=read_count,
            delivered_count=delivered_count,
            member_count=member_count_value,
            read_by_me=own or bool(viewer_receipt and viewer_receipt.read_at),
            delivered_to_me=own
            or bool(viewer_receipt and viewer_receipt.delivered_at),
        )
    return stats


def record_delivery(
    db: Session, *, message: Message, user_ids: list[int]
) -> None:
    """Mark a group message delivered to the currently connected members."""
    recipients = [user_id for user_id in user_ids if user_id != message.sender_id]
    if not recipients:
        return
    existing = {
        receipt.user_id: receipt
        for receipt in db.execute(
            select(MessageReceipt).where(
                MessageReceipt.message_id == message.id,
                MessageReceipt.user_id.in_(recipients),
            )
        ).scalars()
    }
    now = utcnow()
    for user_id in recipients:
        receipt = existing.get(user_id)
        if receipt is None:
            db.add(
                MessageReceipt(
                    message_id=message.id,
                    user_id=user_id,
                    delivered_at=now,
                )
            )
        elif receipt.delivered_at is None:
            receipt.delivered_at = now
    db.flush()


def mark_group_read(
    db: Session,
    *,
    group_id: int,
    reader_id: int,
    last_message_id: int,
) -> GroupReadState:
    """Advance the caller's per-member read watermark in a group."""
    target = db.execute(
        select(Message.id).where(
            Message.id == last_message_id,
            Message.conversation_id == group_id,
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError("Message not found.")

    message_ids = list(
        db.execute(
            select(Message.id).where(
                Message.conversation_id == group_id,
                Message.sender_id != reader_id,
                Message.id <= last_message_id,
            )
        ).scalars()
    )
    existing = {
        receipt.message_id: receipt
        for receipt in db.execute(
            select(MessageReceipt).where(
                MessageReceipt.user_id == reader_id,
                MessageReceipt.message_id.in_(message_ids),
            )
        ).scalars()
    } if message_ids else {}

    now = utcnow()
    updated = 0
    for message_id in message_ids:
        receipt = existing.get(message_id)
        if receipt is None:
            db.add(
                MessageReceipt(
                    message_id=message_id,
                    user_id=reader_id,
                    delivered_at=now,
                    read_at=now,
                )
            )
            updated += 1
        elif receipt.read_at is None:
            receipt.read_at = now
            if receipt.delivered_at is None:
                receipt.delivered_at = now
            updated += 1
    db.flush()
    return GroupReadState(
        last_read_message_id=last_message_id,
        read_at=now if updated else None,
        updated=updated,
        unread_count=group_unread_count(
            db, group_id=group_id, viewer_id=reader_id
        ),
    )


def group_unread_count(db: Session, *, group_id: int, viewer_id: int) -> int:
    return group_unread_counts(
        db, user_id=viewer_id, group_ids=[group_id]
    ).get(group_id, 0)


def group_unread_counts(
    db: Session, *, user_id: int, group_ids: list[int]
) -> dict[int, int]:
    """Unread group messages per conversation, using per-member receipts."""
    if not group_ids:
        return {}
    statement = (
        select(Message.conversation_id, func.count())
        .join(
            ConversationMember,
            ConversationMember.conversation_id == Message.conversation_id,
        )
        .outerjoin(
            MessageReceipt,
            and_(
                MessageReceipt.message_id == Message.id,
                MessageReceipt.user_id == user_id,
            ),
        )
        .where(
            Message.conversation_id.in_(group_ids),
            ConversationMember.user_id == user_id,
            Message.sender_id != user_id,
            MessageReceipt.read_at.is_(None),
        )
        .group_by(Message.conversation_id)
    )
    return {
        int(group_id): int(total)
        for group_id, total in db.execute(statement).all()
    }


def latest_message(db: Session, *, group_id: int) -> Message | None:
    return chat_service.last_message(db, conversation_id=group_id)


def list_messages(
    db: Session, *, group_id: int, limit: int, offset: int
) -> list[Message]:
    return chat_service.list_messages(
        db, conversation_id=group_id, limit=limit, offset=offset
    )


def count_messages(db: Session, *, group_id: int) -> int:
    return chat_service.count_messages(db, conversation_id=group_id)


def send_message(
    db: Session,
    *,
    group: Conversation,
    sender: User,
    body: str | None,
    client_message_id: str | None = None,
    media=None,  # type: ignore[no-untyped-def]
) -> tuple[Message, bool]:
    """Send through the shared chat service, then apply group semantics.

    The shared service handles validation, idempotency, media ownership and the
    last-activity timestamp. It deliberately skips direct-chat block rules for
    groups (a group is multi-party; a pairwise block cannot hide a shared
    conversation from the other members). Invitations are the place where blocks
    are enforced for groups.
    """
    return chat_service.send_message(
        db,
        conversation=group,
        sender=sender,
        body=body,
        client_message_id=client_message_id,
        media=media,
    )
