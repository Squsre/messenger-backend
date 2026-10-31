/**
 * WebSocket frame contract for /api/v1/ws.
 *
 * OpenAPI cannot describe WebSocket frames, so this is derived from the
 * backend README ("Realtime chat (WebSocket)") section — it is the contract.
 */
import type {
  GroupInvitationRead,
  GroupRead,
  GroupRole,
  MessageRead,
  UserPublic,
} from "./api";

/* ------------------------------ Client -> server --------------------------- */

export interface AuthFrame {
  type: "auth";
  token: string;
}

export interface WsMessageFrame {
  type: "message";
  conversation_id: number;
  /** 1–4000 chars when present. */
  body: string | null;
  /** Opaque media id uploaded to the same conversation. */
  media_id: string | null;
  /** UUID v4 for idempotent retries (1–64 chars). */
  client_message_id: string;
}

export interface WsReadFrame {
  type: "read";
  conversation_id: number;
  last_message_id: number;
}

export interface WsTypingFrame {
  type: "typing";
  conversation_id: number;
  is_typing: boolean;
}

export interface WsPingFrame {
  type: "ping";
}

export interface WsPongFrame {
  type: "pong";
}

export type ClientFrame =
  | AuthFrame
  | WsMessageFrame
  | WsReadFrame
  | WsTypingFrame
  | WsPingFrame
  | WsPongFrame;

/* ------------------------------ Server -> client --------------------------- */

/**
 * Identity block of the `ready` frame. The backend sends the public profile
 * plus the owner-only `show_last_seen` flag (no email / id here).
 */
export type ReadyUser = UserPublic & { show_last_seen?: boolean };

export interface ReadyFrame {
  type: "ready";
  connection_id: string;
  user: ReadyUser;
  /** Every conversation id (direct AND group) of the account. */
  conversations: number[];
  server_time: string;
}

export interface MessageFrame {
  type: "message";
  message: MessageRead;
}

export interface MessageUpdatedFrame {
  type: "message_updated";
  message: MessageRead;
}

export interface AckFrame {
  type: "ack";
  client_message_id: string;
  duplicate: boolean;
  message: MessageRead;
}

export interface DeliveredFrame {
  type: "delivered";
  conversation_id: number;
  message_id: number;
  delivered_at: string;
}

export interface ReadFrame {
  type: "read";
  conversation_id: number;
  message_id: number;
  reader_username: string;
  read_at: string;
}

export interface TypingFrame {
  type: "typing";
  conversation_id: number;
  username: string;
  is_typing: boolean;
  expires_in_seconds: number;
}

export interface PingFrame {
  type: "ping";
  server_time: string;
}

export interface PongFrame {
  type: "pong";
  server_time: string;
}

export interface WsErrorFrame {
  type: "error";
  code: string;
  message: string;
  client_message_id: string | null;
  retry_after_seconds: number | null;
}

/* ------------------------- Group lifecycle events -------------------------- */

export interface GroupCreatedFrame {
  type: "group_created";
  group: GroupRead;
}

export interface GroupUpdatedFrame {
  type: "group_updated";
  group: GroupRead;
}

export interface GroupMemberPayload {
  user_id: number;
  username: string;
  role: GroupRole;
}

export interface GroupMemberJoinedFrame {
  type: "group_member_joined";
  group_id: number;
  member: GroupMemberPayload;
}

export interface GroupMemberLeftFrame {
  type: "group_member_left";
  group_id: number;
  username: string;
}

export interface GroupMemberRemovedFrame {
  type: "group_member_removed";
  group_id: number;
  username: string;
}

export interface GroupRoleUpdatedFrame {
  type: "group_role_updated";
  group_id: number;
  member: GroupMemberPayload;
}

export interface GroupOwnerTransferredFrame {
  type: "group_owner_transferred";
  group_id: number;
  owner: GroupMemberPayload;
  previous_owner_username: string;
}

export interface GroupInvitationFrame {
  type: "group_invitation";
  invitation: {
    id: number;
    group_id: number;
    group_name: string;
    inviter_username: string;
    status: "pending";
  };
}

export interface GroupInvitationUpdatedFrame {
  type: "group_invitation_updated";
  invitation: GroupInvitationRead;
}

export type ServerFrame =
  | ReadyFrame
  | MessageFrame
  | MessageUpdatedFrame
  | AckFrame
  | DeliveredFrame
  | ReadFrame
  | TypingFrame
  | PingFrame
  | PongFrame
  | WsErrorFrame
  | GroupCreatedFrame
  | GroupUpdatedFrame
  | GroupMemberJoinedFrame
  | GroupMemberLeftFrame
  | GroupMemberRemovedFrame
  | GroupRoleUpdatedFrame
  | GroupOwnerTransferredFrame
  | GroupInvitationFrame
  | GroupInvitationUpdatedFrame;

/** Close codes used by the backend during the socket lifecycle. */
export const WS_CLOSE_CODES = {
  /** Missing/invalid/expired/revoked token, auth timeout, non-auth first frame. */
  UNAUTHORIZED: 4401,
  /** Account disabled. */
  FORBIDDEN: 4403,
  /** Too many open sockets for the account. */
  TOO_MANY_CONNECTIONS: 4429,
  /** Too many unauthenticated sockets (global or per peer). */
  SERVER_BUSY: 1013,
  /** Idle timeout / shutdown. */
  GOING_AWAY: 1001,
} as const;

export function isServerFrame(value: unknown): value is ServerFrame {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    typeof (value as { type: unknown }).type === "string"
  );
}
