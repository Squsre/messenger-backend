"use client";

/**
 * Realtime engine: a single native WebSocket with a resilient lifecycle.
 *
 * - Handshake: the `auth` frame is the FIRST frame sent, within 5 s of open.
 * - Heartbeat: server `ping` frames are answered with `pong` immediately; the
 *   client also pings every 25 s and closes a stale link (>70 s silent).
 * - Reconnect: exponential backoff (1 s → 30 s) with jitter, reset on `ready`.
 * - 4401/4403 close codes purge the session and redirect to /login.
 * - Frames are dispatched to the message / conversation stores.
 */
import { create } from "zustand";
import type {
  GroupMessageRead,
  MessageRead,
  ReadReceipt,
} from "@/types/api";
import type { ClientFrame, ServerFrame } from "@/types/ws";
import { WS_CLOSE_CODES } from "@/types/ws";
import { apiClient } from "@/lib/api-client";
import {
  READ_THROTTLE_MS,
  TYPING_THROTTLE_MS,
  WS_AUTH_TIMEOUT_MS,
  WS_CLIENT_PING_INTERVAL_MS,
  WS_RECONNECT_MAX_MS,
  WS_RECONNECT_MIN_MS,
  WS_STALE_THRESHOLD_MS,
} from "@/lib/constants";
import { buildWsUrl } from "@/lib/api-client";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/use-auth-store";
import { showMessageNotification } from "@/lib/notifications";
import {
  chatKey,
  useConversationStore,
  type ChatType,
} from "@/stores/use-conversation-store";
import { useMessageStore } from "@/stores/use-message-store";
import { registerStoreReset } from "@/stores/registry";

export type SocketState =
  | "idle"
  | "connecting"
  | "authenticating"
  | "open"
  | "reconnecting"
  | "closed";

interface SocketStore {
  state: SocketState;
  connectionId: string | null;
  reconnectAttempt: number;
  lastError: string | null;

  connect: (token: string) => void;
  disconnect: () => void;
  sendFrame: (frame: ClientFrame) => boolean;
  /** Send a chat message frame; false when the socket is not usable. */
  transportMessage: (payload: {
    conversation_id: number;
    body: string | null;
    media_id: string | null;
    client_message_id: string;
  }) => boolean;
  /** Throttled read watermark frame (never repeats an older id). */
  sendRead: (conversationId: number, lastMessageId: number) => void;
  /** Throttled typing frames; `is_typing: false` is never throttled. */
  sendTyping: (conversationId: number, isTyping: boolean) => void;
  /** True when this conversation is open, visible and focused. */
  isConversationVisible: (conversationId: number) => boolean;
}

/* --------------------------- Module-level plumbing -------------------------- */

let ws: WebSocket | null = null;
let currentToken: string | null = null;
let intentionalClose = false;

let authTimer: ReturnType<typeof setTimeout> | null = null;
let pingTimer: ReturnType<typeof setInterval> | null = null;
let staleTimer: ReturnType<typeof setInterval> | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

let lastFrameAt = 0;
let reconnectAttempt = 0;
/** True while a reconnect cycle is in flight (reset on `ready`). */
let wsWasReconnecting = false;

// Per-conversation throttle bookkeeping.
const lastTypingSentAt = new Map<number, number>();
let lastReadSentId = new Map<number, number>();
let lastReadSentAt = new Map<number, number>();
let pendingReadTimers = new Map<number, ReturnType<typeof setTimeout>>();

function clearTimers(): void {
  if (authTimer) clearTimeout(authTimer);
  if (pingTimer) clearInterval(pingTimer);
  if (staleTimer) clearInterval(staleTimer);
  if (reconnectTimer) clearTimeout(reconnectTimer);
  for (const timer of pendingReadTimers.values()) clearTimeout(timer);
  authTimer = null;
  pingTimer = null;
  staleTimer = null;
  reconnectTimer = null;
  pendingReadTimers = new Map();
}

function myUsername(): string {
  return useAuthStore.getState().user?.username ?? "";
}

function conversationTypeOf(conversationId: number): ChatType | null {
  const { conversations, groups } = useConversationStore.getState();
  if (conversations.some((c) => c.id === conversationId)) return "direct";
  if (groups.some((g) => g.id === conversationId)) return "group";
  return null;
}

function isGroupShape(raw: MessageRead | GroupMessageRead): boolean {
  return "read_count" in raw || "read_by_me" in raw;
}

/** Human preview for a notification: text, or the attachment kind. */
function previewOf(message: MessageRead | GroupMessageRead): string {
  if (message.body) return message.body;
  if (message.media) {
    return message.media.content_type.startsWith("image/")
      ? "Sent a photo"
      : message.media.filename || "Sent a file";
  }
  return "Sent a message";
}

/** Browser notification for an incoming message (direct AND group chats). */
function notifyIncomingMessage(
  message: MessageRead | GroupMessageRead,
  chatType: ChatType
): void {
  const { conversations, groups } = useConversationStore.getState();
  const target =
    chatType === "direct" ? `/c/${message.conversation_id}` : `/g/${message.conversation_id}`;

  let title: string;
  let body: string;
  if (chatType === "direct") {
    const conversation = conversations.find((c) => c.id === message.conversation_id);
    title = conversation
      ? (conversation.participant.display_name ?? conversation.participant.username)
      : message.sender_username;
    body = previewOf(message);
  } else {
    const group = groups.find((g) => g.id === message.conversation_id);
    title = group?.name ?? "Group";
    body = `${message.sender_username}: ${previewOf(message)}`;
  }

  showMessageNotification({
    title,
    body,
    tag: `messenger-${chatType}-${message.conversation_id}`,
    target,
  });
}

/** Would a new incoming message for this chat be unread for the user? */
function countAsUnread(senderUsername: string, conversationId: number): boolean {
  if (senderUsername === myUsername()) return false;
  const store = useSocketStore.getState();
  return !store.isConversationVisible(conversationId);
}

function handleServerFrame(raw: ServerFrame): void {
  const authStore = useAuthStore.getState();
  const me = myUsername();

  switch (raw.type) {
    case "ready": {
      reconnectAttempt = 0;
      if (authTimer) {
        clearTimeout(authTimer);
        authTimer = null;
      }
      useSocketStore.setState({
        state: "open",
        connectionId: raw.connection_id,
        reconnectAttempt: 0,
        lastError: null,
      });
      // Identity check from the ready payload: when the socket identity
      // differs from the cached profile (multi-tab account switch), the
      // refresh re-syncs the private profile; mismatches in username are
      // also caught by the boot probe (GET /auth/me).
      const localUser = authStore.user;
      if (!localUser || localUser.username !== raw.user.username) {
        authStore.refreshProfile().catch(() => undefined);
      }
      // After a reconnect the unread counters may have drifted (messages
      // delivered while the link was down). The server list carries the
      // authoritative `unread_count`, so refresh both lists.
      if (wsWasReconnecting) {
        wsWasReconnecting = false;
        void useConversationStore.getState().loadConversations(true);
        void useConversationStore.getState().loadGroups(true);
      }
      // Reconcile the authoritative conversation id list carried by the
      // ready frame: ids missing from the local lists (page-2 chats beyond
      // the 50-row lists) are fetched via the detail endpoints so presence
      // and the sidebar are complete right after every (re)connect. Skipped
      // during boot while the HTTP lists are still loading.
      void useConversationStore.getState().reconcileServerIds(raw.conversations);
      // Busy accounts own more chats than the 12-probe cap can cover: pull
      // bounded extra pages of BOTH lists until the ready ids are known
      // (READY_CATCHUP_MAX_PAGES rounds max, so the reconnect burst stays
      // small). No-op during boot for the same reason as above.
      void useConversationStore.getState().catchUpLists(raw.conversations);
      // Presence for my own account is refreshed server-side on connect.
      break;
    }

    case "ping": {
      useSocketStore.getState().sendFrame({ type: "pong" });
      break;
    }

    case "pong":
      break;

    case "message": {
      const message = raw.message;
      const isGroup = isGroupShape(message);
      const chatType: ChatType = isGroup ? "group" : "direct";
      const visible = useSocketStore.getState().isConversationVisible(message.conversation_id);
      useMessageStore.getState().applyIncomingMessage({ message, isGroup });
      useConversationStore.getState().applyIncomingMessage({
        chatType,
        conversationId: message.conversation_id,
        message,
        countAsUnread: message.sender_username !== me && !visible,
      });
      // Advance MY read watermark automatically when the chat is on screen.
      if (visible && message.sender_username !== me) {
        useSocketStore.getState().sendRead(message.conversation_id, message.id);
      }
      // Desktop notification for direct AND group messages that arrived
      // while the conversation was not on screen.
      if (message.sender_username !== me && !visible) {
        notifyIncomingMessage(message, chatType);
      }
      break;
    }

    case "message_updated": {
      useMessageStore
        .getState()
        .applyMessageUpdated(raw.message, isGroupShape(raw.message));
      break;
    }

    case "ack": {
      const message = raw.message;
      const isGroup = isGroupShape(message);
      useMessageStore.getState().confirmAck({
        clientMessageId: raw.client_message_id,
        message,
        duplicate: raw.duplicate,
        isGroup,
      });
      // My own send still refreshes the list ordering/preview.
      useConversationStore.getState().applyIncomingMessage({
        chatType: isGroup ? "group" : "direct",
        conversationId: message.conversation_id,
        message,
        countAsUnread: false,
      });
      break;
    }

    case "delivered": {
      useMessageStore.getState().applyDeliveredWatermark({
        conversationId: raw.conversation_id,
        lastMessageId: raw.message_id,
        deliveredAt: raw.delivered_at,
        myUsername: me,
        isGroup: conversationTypeOf(raw.conversation_id) === "group",
      });
      break;
    }

    case "read": {
      const isGroup = conversationTypeOf(raw.conversation_id) === "group";
      useMessageStore.getState().applyReadWatermark({
        conversationId: raw.conversation_id,
        lastMessageId: raw.message_id,
        readAt: raw.read_at,
        readerUsername: raw.reader_username,
        myUsername: me,
        isGroup,
      });
      if (raw.reader_username === me && !isGroup) {
        useConversationStore.getState().markReadLocally("direct", raw.conversation_id);
      }
      if (raw.reader_username === me && isGroup) {
        useConversationStore.getState().markReadLocally("group", raw.conversation_id);
      }
      break;
    }

    case "typing": {
      useMessageStore.getState().setTyping(
        raw.conversation_id,
        raw.username,
        raw.is_typing
      );
      break;
    }

    case "error": {
      if (raw.client_message_id) {
        useMessageStore.getState().markFailed(raw.client_message_id);
      }
      toast.error(raw.message || "The server rejected the last action.", {
        description: raw.code,
      });
      break;
    }

    case "group_created":
    case "group_updated": {
      useConversationStore.getState().upsertGroup(raw.group);
      break;
    }

    case "group_member_joined": {
      const groups = useConversationStore.getState().groups;
      const group = groups.find((g) => g.id === raw.group_id);
      if (group) {
        useConversationStore.getState().upsertGroup({
          ...group,
          member_count: group.member_count + 1,
        });
      }
      toast.info(`${raw.member.username} joined the group`);
      notifyGroupMembersChanged(raw.group_id);
      break;
    }

    case "group_member_left":
    case "group_member_removed": {
      const username = raw.username;
      const state = useConversationStore.getState();
      const group = state.groups.find((g) => g.id === raw.group_id);
      if (group) {
        useConversationStore.getState().upsertGroup({
          ...group,
          member_count: Math.max(1, group.member_count - 1),
        });
      }
      if (username === me) {
        useConversationStore.getState().handleGroupRemoved(raw.group_id);
        toast.info("You are no longer a member of this group.");
      } else {
        toast.info(`${username} left the group`);
        notifyGroupMembersChanged(raw.group_id);
      }
      break;
    }

    case "group_role_updated": {
      const state = useConversationStore.getState();
      const group = state.groups.find((g) => g.id === raw.group_id);
      if (group && raw.member.username === me) {
        useConversationStore.getState().upsertGroup({ ...group, role: raw.member.role });
        toast.info(`You are now ${raw.member.role}`);
      }
      // The open members panel must reflect other members' role changes too.
      notifyGroupMembersChanged(raw.group_id);
      break;
    }

    case "group_owner_transferred": {
      const state = useConversationStore.getState();
      const group = state.groups.find((g) => g.id === raw.group_id);
      if (group) {
        // Keep the sidebar consistent: new owner_username + adjusted roles
        // (previous owner becomes admin, mirroring the backend behavior).
        const myRole =
          raw.owner.username === me
            ? "owner"
            : raw.previous_owner_username === me
              ? "admin"
              : group.role;
        useConversationStore.getState().upsertGroup({
          ...group,
          owner_username: raw.owner.username,
          role: myRole,
        });
      }
      toast.info(
        raw.owner.username === me
          ? "You are now the owner of this group."
          : `@${raw.previous_owner_username} transferred ownership to @${raw.owner.username}`
      );
      notifyGroupMembersChanged(raw.group_id);
      break;
    }

    case "group_invitation": {
      useConversationStore.getState().addPendingInvitation({
        id: raw.invitation.id,
        group_id: raw.invitation.group_id,
        group_name: raw.invitation.group_name,
        inviter_username: raw.invitation.inviter_username,
        invitee_username: me,
        status: "pending",
        created_at: new Date().toISOString(),
        responded_at: null,
      });
      toast.info(
        `${raw.invitation.inviter_username} invited you to ${raw.invitation.group_name}`,
        { action: { label: "View", onClick: () => openInvitationsDialog() } }
      );
      break;
    }

    case "group_invitation_updated": {
      useConversationStore.getState().removeInvitation(raw.invitation.id);
      break;
    }

    default:
      break;
  }
}

function openInvitationsDialog(): void {
  // Imported lazily to keep this module free of ui-store coupling.
  import("@/stores/use-ui-store").then(({ useUiStore }) => {
    useUiStore.getState().setInvitationsOpen(true);
  });
}

/**
 * Tell a currently-open GroupInfo panel that its member list is stale
 * (join / leave / kick / role change / ownership transfer). The panel
 * listens for this event and re-pulls GET /groups/{id}/members.
 */
function notifyGroupMembersChanged(groupId: number): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent("messenger:group-members-changed", {
      detail: { groupId },
    })
  );
}

/**
 * HTTP read-watermark fallback (POST /conversations/{id}/read or
 * POST /groups/{id}/read). Used when the socket cannot deliver the `read`
 * frame so opening a chat still clears the unread counter server-side.
 * The endpoint is idempotent; failures are swallowed (the next successful
 * reconnect resyncs counters from the authoritative lists).
 */
async function postReadRest(
  conversationId: number,
  lastMessageId: number
): Promise<void> {
  const chatType = conversationTypeOf(conversationId);
  if (!chatType) return;
  const path =
    chatType === "group"
      ? `/api/v1/groups/${conversationId}/read`
      : `/api/v1/conversations/${conversationId}/read`;
  try {
    const receipt = await apiClient.post<ReadReceipt>(path, {
      last_message_id: lastMessageId,
    });
    useConversationStore
      .getState()
      .markReadLocally(chatType, conversationId, receipt.unread_count);
  } catch {
    // Offline or rate-limited — resync happens on the next list refresh.
  }
}

function scheduleReconnect(): void {
  const attempt = reconnectAttempt + 1;
  reconnectAttempt = attempt;
  const base = Math.min(WS_RECONNECT_MAX_MS, WS_RECONNECT_MIN_MS * 2 ** (attempt - 1));
  const delay = base + Math.random() * 400;
  useSocketStore.setState({ state: "reconnecting", reconnectAttempt: attempt });
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (currentToken) {
      useSocketStore.getState().connect(currentToken);
    }
  }, delay);
}

function startHeartbeats(): void {
  lastFrameAt = Date.now();
  pingTimer = setInterval(() => {
    useSocketStore.getState().sendFrame({ type: "ping" });
  }, WS_CLIENT_PING_INTERVAL_MS);
  staleTimer = setInterval(() => {
    if (Date.now() - lastFrameAt > WS_STALE_THRESHOLD_MS) {
      // The link is silently dead (no server frame at all) — force a cycle.
      ws?.close(4000, "stale");
    }
  }, 10000);
}

/* --------------------------------- The store -------------------------------- */

export const useSocketStore = create<SocketStore>()((set) => ({
    state: "idle",
    connectionId: null,
    reconnectAttempt: 0,
    lastError: null,

    connect: (token) => {
      if (typeof window === "undefined") return;
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        return;
      }
      currentToken = token;
      intentionalClose = false;
      set({ state: ws ? "reconnecting" : "connecting" });
      if (ws) wsWasReconnecting = true;

      const socket = new WebSocket(buildWsUrl());
      ws = socket;

      socket.onopen = () => {
        set({ state: "authenticating" });
        // Contract: the auth frame MUST be the first frame, within 5 s.
        socket.send(JSON.stringify({ type: "auth", token } satisfies ClientFrame));
        authTimer = setTimeout(() => {
          socket.close(4001, "auth_timeout");
        }, WS_AUTH_TIMEOUT_MS);
      };

      socket.onmessage = (event) => {
        lastFrameAt = Date.now();
        let frame: ServerFrame;
        try {
          frame = JSON.parse(event.data as string) as ServerFrame;
        } catch {
          return;
        }
        try {
          handleServerFrame(frame);
        } catch {
          // A bad frame must never kill the connection loop.
        }
      };

      socket.onclose = (event) => {
        clearTimers();
        ws = null;
        const code = event.code;

        if (intentionalClose) {
          set({ state: "closed" });
          return;
        }

        if (code === WS_CLOSE_CODES.UNAUTHORIZED || code === WS_CLOSE_CODES.FORBIDDEN) {
          set({
            state: "closed",
            lastError: code === WS_CLOSE_CODES.FORBIDDEN ? "Account disabled." : "Session expired.",
          });
          useAuthStore.getState().forceLogout("expired");
          return;
        }

        set({
          lastError:
            code === WS_CLOSE_CODES.TOO_MANY_CONNECTIONS
              ? "Too many connections for this account."
              : null,
        });
        scheduleReconnect();
      };

      socket.onerror = () => {
        set({ lastError: "Realtime connection error." });
      };
    },

    disconnect: () => {
      intentionalClose = true;
      clearTimers();
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close(1000, "client_disconnect");
      }
      ws = null;
      currentToken = null;
      set({ state: "closed", connectionId: null });
    },

    sendFrame: (frame) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      try {
        ws.send(JSON.stringify(frame));
        return true;
      } catch {
        return false;
      }
    },

    transportMessage: (payload) => {
      return useSocketStore.getState().sendFrame({
        type: "message",
        conversation_id: payload.conversation_id,
        body: payload.body,
        media_id: payload.media_id,
        client_message_id: payload.client_message_id,
      });
    },

    sendRead: (conversationId, lastMessageId) => {
      const lastSentId = lastReadSentId.get(conversationId) ?? 0;
      if (lastMessageId <= lastSentId) return;
      const now = Date.now();
      const lastSentAt = lastReadSentAt.get(conversationId) ?? 0;
      const elapsed = now - lastSentAt;

      const deliver = () => {
        lastReadSentId.set(conversationId, lastMessageId);
        lastReadSentAt.set(conversationId, Date.now());
        pendingReadTimers.delete(conversationId);
        const sent = useSocketStore.getState().sendFrame({
          type: "read",
          conversation_id: conversationId,
          last_message_id: lastMessageId,
        });
        // Socket down (or dropped mid-flight): fall back to the REST
        // watermark so read receipts never depend on the WS alone.
        if (!sent) void postReadRest(conversationId, lastMessageId);
      };

      if (elapsed >= READ_THROTTLE_MS) {
        deliver();
        return;
      }
      if (!pendingReadTimers.has(conversationId)) {
        const timer = setTimeout(deliver, READ_THROTTLE_MS - elapsed);
        pendingReadTimers.set(conversationId, timer);
      }
    },

    sendTyping: (conversationId, isTyping) => {
      if (!isTyping) {
        // Stop frames are never throttled (server contract).
        useSocketStore.getState().sendFrame({
          type: "typing",
          conversation_id: conversationId,
          is_typing: false,
        });
        lastTypingSentAt.delete(conversationId);
        return;
      }
      const now = Date.now();
      const last = lastTypingSentAt.get(conversationId) ?? 0;
      if (now - last < TYPING_THROTTLE_MS) return;
      lastTypingSentAt.set(conversationId, now);
      useSocketStore.getState().sendFrame({
        type: "typing",
        conversation_id: conversationId,
        is_typing: true,
      });
    },

    isConversationVisible: (conversationId) => {
      const active = useConversationStore.getState().activeChat;
      if (!active) return false;
      if (active.id !== conversationId) return false;
      if (typeof document === "undefined") return false;
      return document.visibilityState === "visible" && document.hasFocus();
    },
}));

registerStoreReset("socket", () => {
  useSocketStore.getState().disconnect();
  lastTypingSentAt.clear();
  lastReadSentId = new Map();
  lastReadSentAt = new Map();
  pendingReadTimers = new Map();
  reconnectAttempt = 0;
  useSocketStore.setState({
    state: "idle",
    connectionId: null,
    reconnectAttempt: 0,
    lastError: null,
  });
});

/** Convenience helper for components that only need the key of the feed. */
export function feedKeyOf(chatType: ChatType, id: number): string {
  return chatKey(chatType, id);
}

export type { MessageRead, GroupMessageRead };
