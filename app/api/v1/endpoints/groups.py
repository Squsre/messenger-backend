"""Private and public group endpoints.

Routes (all under ``/api/v1/groups``):

* ``POST   /``                                      create a group
* ``GET    /``                                      groups I belong to
* ``GET    /search``                                public group discovery
* ``GET    /invitations``                           my invitations
* ``GET    /{group_id}``                            group details
* ``PATCH  /{group_id}``                            edit group metadata
* ``GET    /{group_id}/members``                    member list
* ``POST   /{group_id}/join``                       join a public group
* ``POST   /{group_id}/invitations``                invite a user
* ``GET    /{group_id}/invitations``                group invitations (manager)
* ``POST   /{group_id}/invitations/{id}/accept``    accept an invitation
* ``POST   /{group_id}/invitations/{id}/decline``   decline an invitation
* ``POST   /{group_id}/invitations/{id}/revoke``    revoke an invitation
* ``DELETE /{group_id}/invitations/{id}``           revoke an invitation
* ``DELETE /{group_id}/members/{user_id}``          remove a member
* ``PATCH  /{group_id}/members/{user_id}``          change a member's role
* ``POST   /{group_id}/transfer-owner``             transfer ownership
* ``POST   /{group_id}/leave``                      leave the group
* ``POST   /{group_id}/messages``                   send a group message
* ``GET    /{group_id}/messages``                   group message history
* ``POST   /{group_id}/read``                       mark group messages read

All routes require Bearer authentication. Group ids, user ids and roles in a
request are only *lookups*; the service re-checks the caller's membership and
role in the database. Private group information and messages are hidden behind
``404`` for non-members. Public group profiles and member lists are visible to
authenticated users, but reading or sending messages still requires membership.

Group message read state is per member (``message_receipts``), unlike direct
chats where a single recipient makes the message-level timestamps sufficient.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import Settings
from app.core.dependencies import get_app_settings
from app.core.errors import BadRequestError, NotFoundError
from app.db.models import (
    Conversation,
    GroupInvitation,
    GroupRole,
    Message,
    User,
)
from app.db.session import get_db
from app.schemas.chat import MarkReadRequest, MessageCreate, ReadReceipt, build_message
from app.schemas.group import (
    GroupCreate,
    GroupInvitationCreate,
    GroupInvitationPage,
    GroupInvitationRead,
    GroupMemberPage,
    GroupMemberRead,
    GroupMemberRoleUpdate,
    GroupMessagePage,
    GroupMessageRead,
    GroupOwnerTransfer,
    GroupPage,
    GroupRead,
    GroupUpdate,
    build_group,
    build_group_invitation,
    build_group_member,
    build_group_message,
    build_group_message_preview,
)
from app.schemas.realtime import (
    GroupCreatedEvent,
    GroupInvitationEvent,
    GroupInvitationUpdatedEvent,
    GroupMemberJoinedEvent,
    GroupMemberLeftEvent,
    GroupMemberRemovedEvent,
    GroupOwnerTransferredEvent,
    GroupRoleUpdatedEvent,
    GroupUpdatedEvent,
    MessageEvent,
    ReadEvent,
)
from app.services import chat as chat_service
from app.services import group as group_service
from app.services import media as media_service
from app.services.user import get_user_by_username

router = APIRouter(prefix="/groups", tags=["groups"])

InvitationStatusQuery = Literal["pending", "accepted", "declined", "revoked"]

_UNAUTHORIZED = {
    "description": "Missing, invalid, expired or revoked credentials.",
}
_FORBIDDEN = {
    "description": "The caller's role does not permit this operation.",
}
_NOT_FOUND = {
    "description": "No such group/member/invitation, or a private group the caller cannot see.",
}
_CONFLICT = {
    "description": "The operation conflicts with the current membership or invitation state.",
}
_VALIDATION = {
    "description": "The submitted payload is invalid.",
}
_RATE_LIMITED = {
    "description": "Too many group requests. Retry after the ``Retry-After`` interval.",
}

_READ_ERRORS = {
    401: _UNAUTHORIZED,
    403: _FORBIDDEN,
    404: _NOT_FOUND,
    409: _CONFLICT,
    422: _VALIDATION,
    429: _RATE_LIMITED,
}


def _owner_username(db: Session, group: Conversation) -> str:
    if group.owner_id is None:
        return ""
    owner = db.get(User, group.owner_id)
    return owner.username if owner is not None else ""


def _member_ids(db: Session, group_id: int) -> list[int]:
    return group_service.member_ids(db, group_id=group_id)


def _last_preview(
    db: Session,
    group: Conversation,
    *,
    include: bool,
) -> tuple[object | None, object | None]:
    """Return ``(preview, media)`` for a group's newest message."""
    if not include:
        return None, None
    message = group_service.latest_message(db, group_id=group.id)
    if message is None:
        return None, None
    media = media_service.media_for_message(db, message_id=message.id)
    sender = db.get(User, message.sender_id)
    preview = build_group_message_preview(
        message,
        sender_username=sender.username if sender is not None else "",
        media=media,
    )
    return preview, media


def _group_detail(
    db: Session, group: Conversation, *, viewer: User
) -> GroupRead:
    membership = group_service.get_membership(
        db, group_id=group.id, user_id=viewer.id
    )
    role = membership.role if membership is not None else None
    if not group_service.can_view_group(group, role=role):
        raise NotFoundError("Group not found.")

    preview = None
    unread = 0
    if membership is not None:
        preview, _ = _last_preview(db, group, include=True)
        unread = group_service.group_unread_count(
            db, group_id=group.id, viewer_id=viewer.id
        )
    return build_group(
        group,
        owner_username=_owner_username(db, group),
        member_count=group_service.member_count(db, group_id=group.id),
        role=role,
        unread_count=unread,
        last_message=preview,  # type: ignore[arg-type]
    )


def _resolve_user(
    db: Session,
    *,
    username: str | None,
    user_id: int | None,
    require_active: bool = True,
) -> User:
    user: User | None = None
    if username is not None:
        user = get_user_by_username(db, username)
    elif user_id is not None:
        user = db.get(User, user_id)
    if user is None or (require_active and not user.is_active):
        raise NotFoundError("User not found.")
    return user


def _invitation_reads(
    db: Session, invitations: list[GroupInvitation]
) -> list[GroupInvitationRead]:
    if not invitations:
        return []
    group_ids = {invitation.conversation_id for invitation in invitations}
    user_ids = {
        user_id
        for invitation in invitations
        for user_id in (invitation.inviter_id, invitation.invitee_id)
    }
    group_names = {
        int(group_id): str(name)
        for group_id, name in db.execute(
            select(Conversation.id, Conversation.name).where(
                Conversation.id.in_(group_ids)
            )
        ).all()
    }
    usernames = group_service.usernames_for(db, user_ids=list(user_ids))
    return [
        build_group_invitation(
            invitation,
            group_name=group_names.get(invitation.conversation_id, ""),
            inviter_username=usernames.get(invitation.inviter_id, ""),
            invitee_username=usernames.get(invitation.invitee_id, ""),
        )
        for invitation in invitations
    ]


async def _broadcast(
    request: Request, *, user_ids: list[int], payload: dict
) -> None:
    manager = getattr(request.app.state, "ws_manager", None)
    if manager is not None and user_ids:
        await manager.broadcast(user_ids=user_ids, payload=payload)


def _commit_before_broadcast(db: Session, request: Request) -> bool:
    manager = getattr(request.app.state, "ws_manager", None)
    if manager is not None:
        db.commit()
        return True
    return False


# --- creation and discovery -------------------------------------------------


@router.post(
    "",
    response_model=GroupRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a group",
    description=(
        "Create a private or public group. The caller becomes the owner and the "
        "first member. Names are trimmed; descriptions are optional."
    ),
    responses=_READ_ERRORS,
)
async def create_group(
    payload: GroupCreate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.create_group(
        db,
        owner=current_user,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility,
    )
    db.flush()
    response = build_group(
        group,
        owner_username=current_user.username,
        member_count=1,
        role=GroupRole.OWNER.value,
    )
    if _commit_before_broadcast(db, request):
        await _broadcast(
            request,
            user_ids=[current_user.id],
            payload=GroupCreatedEvent(group=response).model_dump(mode="json"),
        )
    return response


@router.get(
    "",
    response_model=GroupPage,
    summary="List my groups",
    description=(
        "Page through groups the caller belongs to, most recent activity first. "
        "Each item includes the caller's role, member count, newest-message "
        "preview and unread count."
    ),
    responses=_READ_ERRORS,
)
def list_my_groups(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupPage:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    effective_limit = min(limit, settings.conversation_max_limit)
    rows = group_service.list_user_groups(
        db, user_id=current_user.id, limit=effective_limit, offset=offset
    )
    groups = [group for group, _role in rows]
    group_ids = [group.id for group in groups]
    latest = chat_service.latest_messages(db, conversation_ids=group_ids)
    latest_media = media_service.media_by_message_ids(
        db, message_ids=[message.id for message in latest.values()]
    )
    owner_ids = [group.owner_id for group in groups if group.owner_id is not None]
    sender_ids = [message.sender_id for message in latest.values()]
    usernames = group_service.usernames_for(
        db, user_ids=list({*owner_ids, *sender_ids})
    )
    counts = group_service.member_counts(db, group_ids=group_ids)
    unread = group_service.group_unread_counts(
        db, user_id=current_user.id, group_ids=group_ids
    )

    items = []
    for group, role in rows:
        last = latest.get(group.id)
        preview = None
        if last is not None:
            preview = build_group_message_preview(
                last,
                sender_username=usernames.get(last.sender_id, ""),
                media=latest_media.get(last.id),
            )
        items.append(
            build_group(
                group,
                owner_username=usernames.get(group.owner_id or 0, ""),
                member_count=counts.get(group.id, 0),
                role=role,
                unread_count=unread.get(group.id, 0),
                last_message=preview,
            )
        )
    return GroupPage(
        items=items,
        total=group_service.count_user_groups(db, user_id=current_user.id),
        limit=effective_limit,
        offset=offset,
    )


@router.get(
    "/search",
    response_model=GroupPage,
    summary="Search public groups",
    description=(
        "Search public group names and descriptions. Private groups are never "
        "returned. Public profiles are visible to any authenticated account; "
        "messages still require membership."
    ),
    responses=_READ_ERRORS,
)
def search_public_groups(
    request: Request,
    q: str = Query(min_length=1, max_length=64, description="Search term."),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupPage:
    deps.enforce_rate_limit(request, settings, "search", f"user:{current_user.id}")
    query = q.strip().lower()
    if len(query) < settings.search_min_query_length:
        raise BadRequestError(
            f"Search term must be at least {settings.search_min_query_length} characters."
        )
    effective_limit = min(limit, settings.search_max_limit)
    groups = group_service.search_public_groups(
        db, query=query, limit=effective_limit, offset=offset
    )
    group_ids = [group.id for group in groups]
    roles = group_service.roles_for(
        db, user_id=current_user.id, group_ids=group_ids
    )
    counts = group_service.member_counts(db, group_ids=group_ids)
    owner_ids = [group.owner_id for group in groups if group.owner_id is not None]
    usernames = group_service.usernames_for(db, user_ids=owner_ids)
    return GroupPage(
        items=[
            build_group(
                group,
                owner_username=usernames.get(group.owner_id or 0, ""),
                member_count=counts.get(group.id, 0),
                role=roles.get(group.id),
            )
            for group in groups
        ],
        total=group_service.count_public_groups(db, query=query),
        limit=effective_limit,
        offset=offset,
    )


@router.get(
    "/invitations",
    response_model=GroupInvitationPage,
    summary="List my group invitations",
    description=(
        "Page through invitations addressed to the caller. Optional ``status`` "
        "filters pending, accepted, declined or revoked invitations."
    ),
    responses=_READ_ERRORS,
)
def list_my_invitations(
    request: Request,
    status_filter: InvitationStatusQuery | None = Query(
        default=None, alias="status", description="Optional invitation status."
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupInvitationPage:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    effective_limit = min(limit, settings.conversation_max_limit)
    invitations = group_service.list_my_invitations(
        db,
        user_id=current_user.id,
        status=status_filter,
        limit=effective_limit,
        offset=offset,
    )
    return GroupInvitationPage(
        items=_invitation_reads(db, invitations),
        total=group_service.count_my_invitations(
            db, user_id=current_user.id, status=status_filter
        ),
        limit=effective_limit,
        offset=offset,
    )


@router.get(
    "/{group_id}",
    response_model=GroupRead,
    summary="Get group details",
    description=(
        "Return a group's profile. Private groups return ``404`` to non-members; "
        "public groups are visible but their message content is omitted unless "
        "the caller is a member."
    ),
    responses=_READ_ERRORS,
)
def get_group(
    group_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    return _group_detail(db, group, viewer=current_user)


@router.patch(
    "/{group_id}",
    response_model=GroupRead,
    summary="Edit group information",
    description=(
        "Owners and admins may edit the name and description. Only the owner may "
        "change a group between private and public."
    ),
    responses=_READ_ERRORS,
)
async def update_group(
    group_id: int,
    payload: GroupUpdate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    group_service.update_group(
        db,
        group=group,
        actor_id=current_user.id,
        changes=payload.model_dump(exclude_unset=True),
    )
    db.flush()
    response = _group_detail(db, group, viewer=current_user)
    if _commit_before_broadcast(db, request):
        # The group payload contains the viewer's role and unread count, so build
        # it per recipient instead of broadcasting the actor's view to everyone.
        for member_id in _member_ids(db, group.id):
            member = db.get(User, member_id)
            if member is None:  # pragma: no cover - FK keeps members valid
                continue
            member_view = _group_detail(db, group, viewer=member)
            await _broadcast(
                request,
                user_ids=[member_id],
                payload=GroupUpdatedEvent(group=member_view).model_dump(mode="json"),
            )
    return response


# --- members ----------------------------------------------------------------


@router.get(
    "/{group_id}/members",
    response_model=GroupMemberPage,
    summary="List group members",
    description=(
        "Page through group members, owners first and then admins. Private group "
        "member lists are visible only to members; public member lists are "
        "visible to authenticated users."
    ),
    responses=_READ_ERRORS,
)
def list_group_members(
    group_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupMemberPage:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    membership = group_service.get_membership(
        db, group_id=group.id, user_id=current_user.id
    )
    role = membership.role if membership is not None else None
    if not group_service.can_view_group(group, role=role):
        raise NotFoundError("Group not found.")
    effective_limit = min(limit, settings.conversation_max_limit)
    rows = group_service.list_members(
        db, group_id=group.id, limit=effective_limit, offset=offset
    )
    return GroupMemberPage(
        items=[build_group_member(member, user) for member, user in rows],
        total=group_service.member_count(db, group_id=group.id),
        limit=effective_limit,
        offset=offset,
    )


@router.post(
    "/{group_id}/join",
    response_model=GroupRead,
    status_code=status.HTTP_201_CREATED,
    summary="Join a public group",
    description=(
        "Join a public group directly. Repeating the request returns the existing "
        "membership with ``200`` instead of creating a duplicate. Private groups "
        "require a valid invitation and answer ``403``."
    ),
    responses=_READ_ERRORS,
)
async def join_group(
    group_id: int,
    request: Request,
    response: Response,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    member, created = group_service.join_public_group(
        db, group=group, user_id=current_user.id
    )
    db.flush()
    if not created:
        response.status_code = status.HTTP_200_OK
    result = _group_detail(db, group, viewer=current_user)
    if created and _commit_before_broadcast(db, request):
        await _broadcast(
            request,
            user_ids=_member_ids(db, group.id),
            payload=GroupMemberJoinedEvent(
                group_id=group.id,
                member=build_group_member(member, current_user),
            ).model_dump(mode="json"),
        )
    return result


@router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a group member",
    description=(
        "Owners may remove admins and members; admins may remove ordinary "
        "members. The owner cannot be removed. The removed account stops "
        "receiving group events immediately."
    ),
    responses=_READ_ERRORS,
)
async def remove_group_member(
    group_id: int,
    user_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    member, target = group_service.remove_member(
        db, group=group, actor_id=current_user.id, target_id=user_id
    )
    db.flush()
    removed = build_group_member(member, target)
    remaining = _member_ids(db, group.id)
    if _commit_before_broadcast(db, request):
        await _broadcast(
            request,
            user_ids=remaining,
            payload=GroupMemberRemovedEvent(
                group_id=group.id,
                member=removed,
                actor_username=current_user.username,
            ).model_dump(mode="json"),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{group_id}/members/{user_id}",
    response_model=GroupMemberRead,
    summary="Change a member's role",
    description=(
        "Promote a member to admin or demote an admin to member. Only the owner "
        "may change roles; the owner's role cannot be changed here."
    ),
    responses=_READ_ERRORS,
)
async def update_member_role(
    group_id: int,
    user_id: int,
    payload: GroupMemberRoleUpdate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupMemberRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    member, target = group_service.change_role(
        db,
        group=group,
        actor_id=current_user.id,
        target_id=user_id,
        role=payload.role,
    )
    db.flush()
    result = build_group_member(member, target)
    if _commit_before_broadcast(db, request):
        await _broadcast(
            request,
            user_ids=_member_ids(db, group.id),
            payload=GroupRoleUpdatedEvent(
                group_id=group.id, member=result
            ).model_dump(mode="json"),
        )
    return result


@router.patch(
    "/{group_id}/members/{user_id}/role",
    response_model=GroupMemberRead,
    include_in_schema=False,
    summary="Alias for changing a member's role",
)
async def update_member_role_alias(
    group_id: int,
    user_id: int,
    payload: GroupMemberRoleUpdate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupMemberRead:
    return await update_member_role(
        group_id, user_id, payload, request, current_user, db, settings
    )


@router.post(
    "/{group_id}/transfer-owner",
    response_model=GroupRead,
    summary="Transfer group ownership",
    description=(
        "Transfer ownership to another current member. The new owner receives the "
        "owner role and the previous owner becomes an admin. Only the current "
        "owner may call this."
    ),
    responses=_READ_ERRORS,
)
async def transfer_owner(
    group_id: int,
    payload: GroupOwnerTransfer,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    # Authorize first; a non-owner must not learn whether a target account
    # exists by comparing 403 and 404 responses.
    group_service.require_owner(db, group_id=group.id, user_id=current_user.id)
    target = _resolve_user(
        db,
        username=payload.username,
        user_id=payload.user_id,
        require_active=False,
    )
    new_owner, _old_owner, target_user = group_service.transfer_ownership(
        db, group=group, actor_id=current_user.id, target_id=target.id
    )
    db.flush()
    result = _group_detail(db, group, viewer=current_user)
    if _commit_before_broadcast(db, request):
        await _broadcast(
            request,
            user_ids=_member_ids(db, group.id),
            payload=GroupOwnerTransferredEvent(
                group_id=group.id,
                owner=build_group_member(new_owner, target_user),
                previous_owner_username=current_user.username,
            ).model_dump(mode="json"),
        )
    return result


@router.post(
    "/{group_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Leave a group",
    description=(
        "Leave a group. The owner cannot leave until ownership is transferred; "
        "that rule keeps every group with a valid owner."
    ),
    responses=_READ_ERRORS,
)
async def leave_group(
    group_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    member = group_service.leave_group(db, group=group, user_id=current_user.id)
    db.flush()
    left = build_group_member(member, current_user)
    remaining = _member_ids(db, group.id)
    if _commit_before_broadcast(db, request):
        await _broadcast(
            request,
            user_ids=remaining,
            payload=GroupMemberLeftEvent(
                group_id=group.id, member=left
            ).model_dump(mode="json"),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- invitations ------------------------------------------------------------


@router.post(
    "/{group_id}/invitations",
    response_model=GroupInvitationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a user to a group",
    description=(
        "Owners and admins invite an active account by username or numeric id. "
        "Self-invites, existing members, blocked users and duplicate pending "
        "invitations are rejected."
    ),
    responses=_READ_ERRORS,
)
async def invite_user(
    group_id: int,
    payload: GroupInvitationCreate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupInvitationRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    # Check authorization before resolving the target so a non-manager cannot
    # use the endpoint to probe whether an account exists.
    group_service.require_manager(db, group_id=group.id, user_id=current_user.id)
    target = _resolve_user(
        db, username=payload.username, user_id=payload.user_id
    )
    invitation = group_service.create_invitation(
        db, group=group, inviter_id=current_user.id, target=target
    )
    db.flush()
    result = _invitation_reads(db, [invitation])[0]
    if _commit_before_broadcast(db, request):
        audience = {target.id, current_user.id}
        if group.owner_id is not None:
            audience.add(group.owner_id)
        await _broadcast(
            request,
            user_ids=list(audience),
            payload=GroupInvitationEvent(invitation=result).model_dump(mode="json"),
        )
    return result


@router.get(
    "/{group_id}/invitations",
    response_model=GroupInvitationPage,
    summary="List group invitations",
    description="Owners and admins can page through invitations for their group.",
    responses=_READ_ERRORS,
)
def list_group_invitations(
    group_id: int,
    request: Request,
    status_filter: InvitationStatusQuery | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupInvitationPage:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    group_service.require_manager(db, group_id=group.id, user_id=current_user.id)
    effective_limit = min(limit, settings.conversation_max_limit)
    invitations = group_service.list_group_invitations(
        db,
        group_id=group.id,
        status=status_filter,
        limit=effective_limit,
        offset=offset,
    )
    return GroupInvitationPage(
        items=_invitation_reads(db, invitations),
        total=group_service.count_group_invitations(
            db, group_id=group.id, status=status_filter
        ),
        limit=effective_limit,
        offset=offset,
    )


@router.post(
    "/{group_id}/invitations/{invitation_id}/accept",
    response_model=GroupInvitationRead,
    summary="Accept a group invitation",
    description=(
        "Accept a pending invitation addressed to the caller. The caller becomes "
        "a member; a repeated accept after membership is idempotent."
    ),
    responses=_READ_ERRORS,
)
async def accept_group_invitation(
    group_id: int,
    invitation_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupInvitationRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    invitation = group_service.require_invitation_for_invitee(
        db,
        group_id=group.id,
        invitation_id=invitation_id,
        user_id=current_user.id,
    )
    member, created = group_service.accept_invitation(
        db, group=group, invitation=invitation
    )
    db.flush()
    result = _invitation_reads(db, [invitation])[0]
    if _commit_before_broadcast(db, request):
        audience = {invitation.invitee_id, invitation.inviter_id}
        if group.owner_id is not None:
            audience.add(group.owner_id)
        await _broadcast(
            request,
            user_ids=list(audience),
            payload=GroupInvitationUpdatedEvent(
                invitation=result
            ).model_dump(mode="json"),
        )
        if created:
            await _broadcast(
                request,
                user_ids=_member_ids(db, group.id),
                payload=GroupMemberJoinedEvent(
                    group_id=group.id,
                    member=build_group_member(member, current_user),
                ).model_dump(mode="json"),
            )
    return result


@router.post(
    "/{group_id}/invitations/{invitation_id}/decline",
    response_model=GroupInvitationRead,
    summary="Decline a group invitation",
    description="Decline a pending invitation addressed to the caller.",
    responses=_READ_ERRORS,
)
async def decline_group_invitation(
    group_id: int,
    invitation_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupInvitationRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    invitation = group_service.require_invitation_for_invitee(
        db,
        group_id=group.id,
        invitation_id=invitation_id,
        user_id=current_user.id,
    )
    group_service.decline_invitation(invitation)
    db.flush()
    result = _invitation_reads(db, [invitation])[0]
    if _commit_before_broadcast(db, request):
        audience = {invitation.invitee_id, invitation.inviter_id}
        if group.owner_id is not None:
            audience.add(group.owner_id)
        await _broadcast(
            request,
            user_ids=list(audience),
            payload=GroupInvitationUpdatedEvent(
                invitation=result
            ).model_dump(mode="json"),
        )
    return result


async def _revoke(
    group_id: int,
    invitation_id: int,
    request: Request,
    current_user: User,
    db: Session,
    settings: Settings,
) -> GroupInvitationRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group = group_service.require_group(db, group_id)
    group_service.require_manager(db, group_id=group.id, user_id=current_user.id)
    invitation = group_service.get_invitation(
        db, group_id=group.id, invitation_id=invitation_id
    )
    if invitation is None:
        raise NotFoundError("Invitation not found.")
    group_service.revoke_invitation(
        db, group=group, actor_id=current_user.id, invitation=invitation
    )
    db.flush()
    result = _invitation_reads(db, [invitation])[0]
    if _commit_before_broadcast(db, request):
        audience = {invitation.invitee_id, invitation.inviter_id}
        if group.owner_id is not None:
            audience.add(group.owner_id)
        await _broadcast(
            request,
            user_ids=list(audience),
            payload=GroupInvitationUpdatedEvent(
                invitation=result
            ).model_dump(mode="json"),
        )
    return result


@router.post(
    "/{group_id}/invitations/{invitation_id}/revoke",
    response_model=GroupInvitationRead,
    summary="Revoke a group invitation",
    description="Owners and admins revoke a pending invitation.",
    responses=_READ_ERRORS,
)
async def revoke_group_invitation(
    group_id: int,
    invitation_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupInvitationRead:
    return await _revoke(
        group_id, invitation_id, request, current_user, db, settings
    )


@router.delete(
    "/{group_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a group invitation (DELETE form)",
    description="Equivalent to POST /revoke, for clients that prefer DELETE.",
    responses=_READ_ERRORS,
)
async def delete_group_invitation(
    group_id: int,
    invitation_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    await _revoke(group_id, invitation_id, request, current_user, db, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- group messages ---------------------------------------------------------


def _group_message_rows(
    db: Session,
    *,
    messages: list[Message],
    viewer_id: int,
    member_count_value: int,
    media_by_message: dict[int, object],
) -> list[GroupMessageRead]:
    stats = group_service.message_stats(
        db,
        messages=messages,
        viewer_id=viewer_id,
        member_count_value=member_count_value,
    )
    sender_names = group_service.usernames_for(
        db, user_ids=list({message.sender_id for message in messages})
    )
    items = []
    for message in messages:
        message_stats = stats[message.id]
        items.append(
            build_group_message(
                message,
                sender_username=sender_names.get(message.sender_id, ""),
                media=media_by_message.get(message.id),
                read_count=message_stats.read_count,
                delivered_count=message_stats.delivered_count,
                member_count=message_stats.member_count,
                read_by_me=message_stats.read_by_me,
                delivered_to_me=message_stats.delivered_to_me,
            )
        )
    return items


@router.post(
    "/{group_id}/messages",
    response_model=GroupMessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Send a group message",
    description=(
        "Send text, media, or both to a group. Only members may send. The body "
        "follows the same 1-4000 character rule as direct chat. Media must be "
        "uploaded by the sender to the same group conversation."
    ),
    responses=_READ_ERRORS,
)
async def send_group_message(
    group_id: int,
    payload: MessageCreate,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupMessageRead:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group, _member = group_service.require_member(
        db, group_id=group_id, user_id=current_user.id
    )
    media = None
    if payload.media_id is not None:
        media = media_service.require_owned_media(
            db,
            public_id=payload.media_id,
            conversation_id=group.id,
            uploader_id=current_user.id,
        )
    message, created = group_service.send_message(
        db,
        group=group,
        sender=current_user,
        body=payload.body,
        media=media,
    )
    member_ids = group_service.member_ids(db, group_id=group.id)
    manager = getattr(request.app.state, "ws_manager", None)
    if created and manager is not None:
        online = [
            user_id
            for user_id in member_ids
            if user_id != current_user.id and manager.connection_count(user_id)
        ]
        group_service.record_delivery(db, message=message, user_ids=online)
    db.flush()
    if media is None:
        media = media_service.media_for_message(db, message_id=message.id)
    total_members = group_service.member_count(db, group_id=group.id)
    stats = group_service.message_stats(
        db,
        messages=[message],
        viewer_id=current_user.id,
        member_count_value=total_members,
    )[message.id]
    result = build_group_message(
        message,
        sender_username=current_user.username,
        media=media,
        read_count=stats.read_count,
        delivered_count=stats.delivered_count,
        member_count=stats.member_count,
        read_by_me=stats.read_by_me,
        delivered_to_me=stats.delivered_to_me,
    )
    if created and _commit_before_broadcast(db, request):
        event_message = build_message(
            message, sender_username=current_user.username, media=media
        )
        await _broadcast(
            request,
            user_ids=member_ids,
            payload=MessageEvent(message=event_message).model_dump(mode="json"),
        )
    return result


@router.get(
    "/{group_id}/messages",
    response_model=GroupMessagePage,
    summary="Read group message history",
    description=(
        "Return a chronological page of group messages. Only members may read "
        "them. Each item includes per-member read/delivery counts; the caller's "
        "own receipt state is in ``read_by_me``/``delivered_to_me``."
    ),
    responses=_READ_ERRORS,
)
def list_group_messages(
    group_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> GroupMessagePage:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group, _member = group_service.require_member(
        db, group_id=group_id, user_id=current_user.id
    )
    effective_limit = min(limit, settings.message_max_limit)
    messages = group_service.list_messages(
        db, group_id=group.id, limit=effective_limit, offset=offset
    )
    media_by_message = media_service.media_by_message_ids(
        db, message_ids=[message.id for message in messages]
    )
    items = _group_message_rows(
        db,
        messages=messages,
        viewer_id=current_user.id,
        member_count_value=group_service.member_count(db, group_id=group.id),
        media_by_message=media_by_message,
    )
    return GroupMessagePage(
        items=items,
        total=group_service.count_messages(db, group_id=group.id),
        limit=effective_limit,
        offset=offset,
    )


@router.post(
    "/{group_id}/read",
    response_model=ReadReceipt,
    summary="Mark group messages read",
    description=(
        "Advance the caller's per-member read watermark in a group. Other "
        "members' read state is not changed. The update is broadcast as a "
        "``read`` WebSocket event to group members."
    ),
    responses=_READ_ERRORS,
)
async def mark_group_read(
    group_id: int,
    payload: MarkReadRequest,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ReadReceipt:
    deps.enforce_rate_limit(request, settings, "chat", f"user:{current_user.id}")
    group, _member = group_service.require_member(
        db, group_id=group_id, user_id=current_user.id
    )
    state = group_service.mark_group_read(
        db,
        group_id=group.id,
        reader_id=current_user.id,
        last_message_id=payload.last_message_id,
    )
    if state.updated and state.read_at is not None:
        member_ids = group_service.member_ids(db, group_id=group.id)
        if _commit_before_broadcast(db, request):
            await _broadcast(
                request,
                user_ids=member_ids,
                payload=ReadEvent(
                    conversation_id=group.id,
                    message_id=state.last_read_message_id,
                    reader_username=current_user.username,
                    read_at=state.read_at,
                ).model_dump(mode="json"),
            )
    return ReadReceipt(
        conversation_id=group.id,
        last_read_message_id=state.last_read_message_id,
        read_at=state.read_at,
        updated=state.updated,
        unread_count=state.unread_count,
    )
