"""Group request/response schemas.

The public API keeps numeric user ids private for profiles, but group management
needs a stable identifier for member operations (the path is explicitly
``/members/{user_id}``). Group member responses therefore expose ``user_id``
only to callers allowed to see that group's members; direct chat responses are
unchanged.

Group messages deliberately have their own response shape: a group message has
one receipt per member, so the single ``delivered_at``/``read_at`` pair from the
direct-chat model would be misleading. Counts and the viewer's own receipt state
are exposed instead, while the core message fields stay the same.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.security import (
    GROUP_DESCRIPTION_MAX_LENGTH,
    GROUP_NAME_MAX_LENGTH,
    GROUP_NAME_MIN_LENGTH,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    normalize_username,
)
from app.db.models import (
    Conversation,
    GroupRole,
    GroupVisibility,
    InvitationStatus,
    Message,
)
from app.schemas.media import MediaRead, build_media

GroupRoleValue = Literal["owner", "admin", "member"]
GroupVisibilityValue = Literal["private", "public"]
InvitationStatusValue = Literal["pending", "accepted", "declined", "revoked"]


def _clean_group_text(value: str, *, field: str, max_length: int) -> str:
    """Trim a display string and reject empty/control-character content."""
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be empty.")
    if len(text) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters.")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise ValueError(f"{field} must not contain control characters.")
    return text


class GroupCreate(BaseModel):
    """Create a private or public group."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=GROUP_NAME_MIN_LENGTH,
        max_length=GROUP_NAME_MAX_LENGTH,
        description="Display name of the group.",
        examples=["Weekend Hikers"],
    )
    description: str | None = Field(
        default=None,
        max_length=GROUP_DESCRIPTION_MAX_LENGTH,
        description="Optional short description.",
        examples=["Plans for Saturday morning hikes."],
    )
    visibility: GroupVisibilityValue = Field(
        default="private",
        description=(
            "``private`` groups are discoverable only through a valid invitation; "
            "``public`` groups appear in search and can be joined directly."
        ),
        examples=["private"],
    )

    @field_validator("name")
    @classmethod
    def _normalise_name(cls, value: str) -> str:
        return _clean_group_text(
            value, field="Group name", max_length=GROUP_NAME_MAX_LENGTH
        )

    @field_validator("description")
    @classmethod
    def _normalise_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if len(text) > GROUP_DESCRIPTION_MAX_LENGTH:
            raise ValueError(
                "Group description must be at most "
                f"{GROUP_DESCRIPTION_MAX_LENGTH} characters."
            )
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
            raise ValueError("Group description must not contain control characters.")
        return text


class GroupUpdate(BaseModel):
    """Edit the mutable fields of a group."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=GROUP_NAME_MIN_LENGTH,
        max_length=GROUP_NAME_MAX_LENGTH,
    )
    description: str | None = Field(
        default=None, max_length=GROUP_DESCRIPTION_MAX_LENGTH
    )
    visibility: GroupVisibilityValue | None = None

    @field_validator("name")
    @classmethod
    def _normalise_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Group name cannot be cleared.")
        return _clean_group_text(
            value, field="Group name", max_length=GROUP_NAME_MAX_LENGTH
        )

    @field_validator("description")
    @classmethod
    def _normalise_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if len(text) > GROUP_DESCRIPTION_MAX_LENGTH:
            raise ValueError(
                "Group description must be at most "
                f"{GROUP_DESCRIPTION_MAX_LENGTH} characters."
            )
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
            raise ValueError("Group description must not contain control characters.")
        return text

    @field_validator("visibility")
    @classmethod
    def _visibility_cannot_be_cleared(
        cls, value: GroupVisibilityValue | None
    ) -> GroupVisibilityValue:
        if value is None:
            raise ValueError("Group visibility cannot be cleared.")
        return value

    @model_validator(mode="after")
    def _require_a_change(self) -> "GroupUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class GroupMemberRead(BaseModel):
    """One member of a group, with the role verified by the server."""

    user_id: int
    username: str
    display_name: str
    avatar_key: str | None = None
    role: GroupRoleValue
    joined_at: datetime


class GroupMessagePreview(BaseModel):
    """Small preview of the newest group message for group lists."""

    id: int
    sender_username: str
    body: str | None = None
    media: MediaRead | None = None
    created_at: datetime


class GroupRead(BaseModel):
    """A group as seen by one caller."""

    id: int
    name: str
    description: str | None = None
    visibility: GroupVisibilityValue
    owner_username: str
    member_count: int
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    last_message: GroupMessagePreview | None = Field(
        default=None,
        description=(
            "Newest message preview. Only members receive it; public discovery "
            "results omit message content."
        ),
    )
    is_member: bool = False
    role: GroupRoleValue | None = Field(
        default=None,
        description="The caller's role, or null when they are not a member.",
    )
    unread_count: int = Field(
        default=0,
        description="Messages from other members the caller has not read.",
    )


class GroupPage(BaseModel):
    """A page of groups plus paging metadata."""

    items: list[GroupRead]
    total: int
    limit: int
    offset: int


class GroupMemberPage(BaseModel):
    """A page of group members plus paging metadata."""

    items: list[GroupMemberRead]
    total: int
    limit: int
    offset: int


class GroupMessageRead(BaseModel):
    """A group message with per-member delivery/read aggregates.

    ``delivered_at``/``read_at`` mirror the direct-chat fields for clients that
    share a message component. They stay null for groups; use the counts and the
    ``read_by_me``/``delivered_to_me`` flags instead.
    """

    id: int
    conversation_id: int
    sender_username: str
    body: str | None = None
    media: MediaRead | None = None
    created_at: datetime
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    read_count: int = Field(
        default=0, description="Number of other members who read this message."
    )
    delivered_count: int = Field(
        default=0, description="Number of other members who received this message."
    )
    member_count: int = Field(
        default=0, description="Total members in the group, including the sender."
    )
    read_by_me: bool = False
    delivered_to_me: bool = False


class GroupMessagePage(BaseModel):
    """A chronological page of group messages plus paging metadata."""

    items: list[GroupMessageRead]
    total: int
    limit: int
    offset: int


class GroupInvitationCreate(BaseModel):
    """Invite an existing account by username or numeric id."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(
        default=None,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        description="Username of the invitee.",
    )
    user_id: int | None = Field(
        default=None,
        ge=1,
        description="Numeric id of the invitee (alternative to username).",
    )

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "GroupInvitationCreate":
        if (self.username is None) == (self.user_id is None):
            raise ValueError("Provide exactly one of username or user_id.")
        return self

    @field_validator("username")
    @classmethod
    def _normalise_username(cls, value: str | None) -> str | None:
        return normalize_username(value) if value is not None else None


class GroupInvitationRead(BaseModel):
    """An invitation and its current state."""

    id: int
    group_id: int
    group_name: str
    inviter_username: str
    invitee_username: str
    status: InvitationStatusValue
    created_at: datetime
    responded_at: datetime | None = None


class GroupInvitationPage(BaseModel):
    """A page of invitations plus paging metadata."""

    items: list[GroupInvitationRead]
    total: int
    limit: int
    offset: int


class GroupMemberRoleUpdate(BaseModel):
    """Promote a member to admin or demote an admin back to member."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "member"] = Field(
        description="The new role. Ownership is changed with the transfer endpoint."
    )


class GroupOwnerTransfer(BaseModel):
    """Transfer ownership to another current member."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(
        default=None,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
    )
    user_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "GroupOwnerTransfer":
        if (self.username is None) == (self.user_id is None):
            raise ValueError("Provide exactly one of username or user_id.")
        return self

    @field_validator("username")
    @classmethod
    def _normalise_username(cls, value: str | None) -> str | None:
        return normalize_username(value) if value is not None else None


def build_group_member(member, user) -> GroupMemberRead:  # type: ignore[no-untyped-def]
    """Convert a membership row and its user to the group-member shape."""
    return GroupMemberRead(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        avatar_key=user.avatar_key,
        role=GroupRole(member.role).value,  # type: ignore[arg-type]
        joined_at=member.created_at,
    )


def build_group_message_preview(
    message: Message,
    *,
    sender_username: str,
    media=None,  # type: ignore[no-untyped-def]
) -> GroupMessagePreview:
    """Convert a message to the compact preview used in group lists."""
    return GroupMessagePreview(
        id=message.id,
        sender_username=sender_username,
        body=message.body,
        media=(
            build_media(media, uploader_username=sender_username)
            if media is not None
            else None
        ),
        created_at=message.created_at,
    )


def build_group(
    conversation: Conversation,
    *,
    owner_username: str,
    member_count: int,
    role: str | None = None,
    unread_count: int = 0,
    last_message: GroupMessagePreview | None = None,
) -> GroupRead:
    """Convert a group conversation to its API representation."""
    if conversation.visibility is None:
        # A malformed group row is not useful to the caller. The service checks
        # this before calling the builder; this fallback keeps serialisation
        # deterministic for defensive callers.
        visibility = GroupVisibility.PRIVATE
    else:
        visibility = GroupVisibility(conversation.visibility)
    return GroupRead(
        id=conversation.id,
        name=conversation.name or "",
        description=conversation.description,
        visibility=visibility.value,  # type: ignore[arg-type]
        owner_username=owner_username,
        member_count=member_count,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        last_message_at=conversation.last_message_at,
        last_message=last_message,
        is_member=role is not None,
        role=GroupRole(role).value if role is not None else None,  # type: ignore[arg-type]
        unread_count=unread_count,
    )


def build_group_message(
    message: Message,
    *,
    sender_username: str,
    media=None,  # type: ignore[no-untyped-def]
    read_count: int = 0,
    delivered_count: int = 0,
    member_count: int = 0,
    read_by_me: bool = False,
    delivered_to_me: bool = False,
) -> GroupMessageRead:
    """Convert a stored message to a group-message response."""
    return GroupMessageRead(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_username=sender_username,
        body=message.body,
        media=(
            build_media(media, uploader_username=sender_username)
            if media is not None
            else None
        ),
        created_at=message.created_at,
        delivered_at=message.delivered_at,
        read_at=message.read_at,
        read_count=read_count,
        delivered_count=delivered_count,
        member_count=member_count,
        read_by_me=read_by_me,
        delivered_to_me=delivered_to_me,
    )


def build_group_invitation(
    invitation,
    *,
    group_name: str,
    inviter_username: str,
    invitee_username: str,
) -> GroupInvitationRead:  # type: ignore[no-untyped-def]
    """Convert an invitation row to its API representation."""
    return GroupInvitationRead(
        id=invitation.id,
        group_id=invitation.conversation_id,
        group_name=group_name,
        inviter_username=inviter_username,
        invitee_username=invitee_username,
        status=InvitationStatus(invitation.status).value,  # type: ignore[arg-type]
        created_at=invitation.created_at,
        responded_at=invitation.responded_at,
    )

