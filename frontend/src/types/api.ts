/**
 * TypeScript definitions derived from the Messenger backend OpenAPI schema
 * (openapi.json) and README.md. Keep in sync with the FastAPI schemas.
 */

/* ---------------------------------- Users --------------------------------- */

/** Who may view a full public profile. */
export type ProfileVisibility = "public" | "contacts" | "private";

/** Public profile of a user (never contains email or account state). */
export interface UserPublic {
  username: string;
  /** Name to show; falls back to the username. */
  display_name: string | null;
  bio: string | null;
  /** Opaque storage key of the avatar, or null when unset. */
  avatar_key: string | null;
  profile_visibility: ProfileVisibility;
  created_at: string;
  /** Last activity, or null when the owner hides it. */
  last_seen_at: string | null;
}

/** The account owner's own view of their profile. */
export interface UserPrivate extends UserPublic {
  id: number;
  email: string;
  is_active: boolean;
  email_verified: boolean;
  show_last_seen: boolean;
  updated_at: string;
}

/** Minimal user representation returned by /auth/register. */
export interface UserRead {
  id: number;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
}

export interface UserPage {
  items: UserPublic[];
  total: number;
  limit: number;
  offset: number;
}

/* ------------------------------- Authentication ---------------------------- */

export interface UserCreate {
  email: string;
  password: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  /** Token lifetime in seconds (900 = 15 minutes). */
  expires_in: number;
}

export interface MessageResponse {
  message: string;
}

export interface PasswordChangeRequest {
  current_password: string;
  new_password: string;
}

export interface PasswordResetRequest {
  email: string;
}

export interface PasswordResetConfirm {
  token: string;
  new_password: string;
}

export interface EmailVerificationConfirm {
  token: string;
}

export interface ProfileUpdate {
  username?: string | null;
  display_name?: string | null;
  bio?: string | null;
  profile_visibility?: ProfileVisibility | null;
  show_last_seen?: boolean | null;
}

export interface PrivacySettingsUpdate {
  profile_visibility?: ProfileVisibility | null;
  show_last_seen?: boolean | null;
}

/* --------------------------------- Sessions -------------------------------- */

export interface SessionRead {
  id: number;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_active_at: string;
  expires_at: string;
  current: boolean;
}

export interface SessionList {
  items: SessionRead[];
  total: number;
}

/* ---------------------------------- Media ---------------------------------- */

export type MediaStatus = "pending" | "ready" | "failed";

/** Metadata for one stored file (storage key intentionally absent). */
export interface MediaRead {
  id: string;
  conversation_id: number;
  uploader_username: string;
  filename: string;
  /** Type detected from the file content. */
  content_type: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  status: MediaStatus;
  checksum_sha256: string | null;
  message_id: number | null;
  created_at: string;
}

export interface MediaBatchRead {
  items: MediaRead[];
}

export interface MediaAccessUrlRead {
  media_id: string;
  url: string;
  expires_in_seconds: number;
}

export interface AttachMediaRequest {
  message_id: number;
}

/* -------------------------------- Direct chat ------------------------------ */

export interface MessageCreate {
  body?: string | null;
  media_id?: string | null;
}

/** A stored direct message, including the recipient's status timestamps. */
export interface MessageRead {
  id: number;
  conversation_id: number;
  sender_username: string;
  body: string | null;
  media: MediaRead | null;
  created_at: string;
  /** null until pushed to a live socket of the recipient or marked read. */
  delivered_at: string | null;
  /** null until the other participant explicitly read it. */
  read_at: string | null;
}

export interface MessagePage {
  items: MessageRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface MarkReadRequest {
  last_message_id: number;
}

export interface ReadReceipt {
  conversation_id: number;
  last_read_message_id: number;
  read_at: string | null;
  updated: number;
  unread_count: number;
}

export interface ConversationCreate {
  username: string;
}

export interface ConversationRead {
  id: number;
  /** The other participant. */
  participant: UserPublic;
  last_message: MessageRead | null;
  created_at: string;
  last_message_at: string | null;
  unread_count: number;
}

export interface ConversationDetail extends ConversationRead {
  message_count: number;
}

export interface ConversationPage {
  items: ConversationRead[];
  total: number;
  limit: number;
  offset: number;
}

/* ---------------------------------- Groups --------------------------------- */

export type GroupVisibility = "private" | "public";
export type GroupRole = "owner" | "admin" | "member";
export type InvitationStatus = "pending" | "accepted" | "declined" | "revoked";

export interface GroupCreate {
  name: string;
  description?: string | null;
  visibility?: GroupVisibility;
}

export interface GroupUpdate {
  name?: string | null;
  description?: string | null;
  visibility?: GroupVisibility | null;
}

export interface GroupMessagePreview {
  id: number;
  sender_username: string;
  body: string | null;
  media: MediaRead | null;
  created_at: string;
}

export interface GroupRead {
  id: number;
  name: string;
  description: string | null;
  visibility: GroupVisibility;
  owner_username: string;
  member_count: number;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  /** Only members receive it; public discovery results omit message content. */
  last_message: GroupMessagePreview | null;
  is_member: boolean;
  /** The caller's role, or null when they are not a member. */
  role: GroupRole | null;
  unread_count: number;
}

export interface GroupPage {
  items: GroupRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface GroupMemberRead {
  user_id: number;
  username: string;
  display_name: string;
  avatar_key: string | null;
  role: GroupRole;
  joined_at: string;
}

export interface GroupMemberPage {
  items: GroupMemberRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface GroupMemberRoleUpdate {
  role: "admin" | "member";
}

export interface GroupOwnerTransfer {
  username?: string | null;
  user_id?: number | null;
}

export interface GroupInvitationCreate {
  username?: string | null;
  user_id?: number | null;
}

export interface GroupInvitationRead {
  id: number;
  group_id: number;
  group_name: string;
  inviter_username: string;
  invitee_username: string;
  status: InvitationStatus;
  created_at: string;
  responded_at: string | null;
}

export interface GroupInvitationPage {
  items: GroupInvitationRead[];
  total: number;
  limit: number;
  offset: number;
}

/** A group message with per-member delivery/read aggregates. */
export interface GroupMessageRead {
  id: number;
  conversation_id: number;
  sender_username: string;
  body: string | null;
  media: MediaRead | null;
  created_at: string;
  /** Retained for client compatibility but null for groups. */
  delivered_at: string | null;
  read_at: string | null;
  /** Number of other members who read this message. */
  read_count: number;
  /** Number of other members who received this message. */
  delivered_count: number;
  /** Total members in the group, including the sender. */
  member_count: number;
  read_by_me: boolean;
  delivered_to_me: boolean;
}

export interface GroupMessagePage {
  items: GroupMessageRead[];
  total: number;
  limit: number;
  offset: number;
}

/* ---------------------------------- Health --------------------------------- */

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
}

/** Banner returned by the API root (`GET /`). */
export interface RootBanner {
  name: string;
  version: string;
  docs_url: string;
  health_url: string;
}

export interface ReadinessResponse {
  status: string;
  checks: Record<string, string>;
}

/* ------------------------------- Error envelope ---------------------------- */

export interface ApiErrorDetail {
  type: string;
  loc: (string | number)[];
  msg: string;
}

/** The uniform error envelope returned by the backend. */
export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: ApiErrorDetail[];
  };
}

export interface PageQuery {
  limit?: number;
  offset?: number;
}
