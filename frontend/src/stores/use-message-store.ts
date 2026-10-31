"use client";

/**
 * Per-chat message feeds with optimistic delivery.
 *
 * Status lifecycle:
 *   pending (clock) -> sent (single tick, from `ack`)
 *                   -> delivered (double gray, from `delivered` watermark)
 *                   -> read (double accent, from `read` watermark)
 * Group feeds instead track read_count / delivered_count / member_count.
 *
 * Watermarks: `delivered`/`read` events carry the last message id; every
 * message with `id <= last_message_id` is updated in one pass.
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type {
  GroupMessageRead,
  MediaRead,
  MessageRead,
} from "@/types/api";
import { apiClient } from "@/lib/api-client";
import {
  isAllowedUpload,
  MEDIA_MAX_UPLOAD_BYTES,
  MEDIA_UPLOAD_TIMEOUT_MS,
  MESSAGE_PAGE_LIMIT,
  TYPING_EXPIRY_MS,
} from "@/lib/constants";
import { uuidv4 } from "@/lib/utils";
import {
  chatKey,
  useConversationStore,
  type ChatType,
} from "@/stores/use-conversation-store";
import { registerStoreReset } from "@/stores/registry";

export type MessageStatus = "pending" | "sent" | "delivered" | "read" | "failed";

export interface ChatMessage {
  /** Server id; null while the message is still optimistic. */
  id: number | null;
  clientMessageId: string;
  conversationId: number;
  chatType: ChatType;
  senderUsername: string;
  body: string | null;
  media: MediaRead | null;
  createdAt: string;
  deliveredAt: string | null;
  readAt: string | null;
  /** Group aggregates (0 for direct chats). */
  readCount: number;
  deliveredCount: number;
  memberCount: number;
  readByMe: boolean;
  status: MessageStatus;
}

export interface ChatFeed {
  messages: ChatMessage[];
  total: number;
  /** History offset of the oldest loaded message (for reverse paging). */
  oldestLoadedOffset: number;
  hasOlder: boolean;
  loading: boolean;
  loadingOlder: boolean;
  error: string | null;
  /** username -> typing expiry (epoch ms). */
  typing: Record<string, number>;
  /** Per-reader read watermark, to avoid double-counting group reads. */
  lastReadByReader: Record<string, number>;
}

interface MessageState {
  feeds: Record<string, ChatFeed>;

  loadFeed: (chatType: ChatType, conversationId: number, force?: boolean) => Promise<void>;
  loadOlder: (chatType: ChatType, conversationId: number) => Promise<void>;
  sendMessage: (input: {
    chatType: ChatType;
    conversationId: number;
    body: string | null;
    mediaId: string | null;
    /** Socket-aware sender injected by the socket store. */
    transport: (frame: {
      conversation_id: number;
      body: string | null;
      media_id: string | null;
      client_message_id: string;
    }) => boolean;
  }) => Promise<void>;
  retryMessage: (input: {
    chatType: ChatType;
    conversationId: number;
    clientMessageId: string;
    transport: (frame: {
      conversation_id: number;
      body: string | null;
      media_id: string | null;
      client_message_id: string;
    }) => boolean;
  }) => Promise<void>;
  confirmAck: (input: {
    clientMessageId: string;
    message: MessageRead | GroupMessageRead;
    duplicate: boolean;
    isGroup: boolean;
  }) => void;
  applyIncomingMessage: (input: {
    message: MessageRead | GroupMessageRead;
    isGroup: boolean;
  }) => void;
  applyMessageUpdated: (message: MessageRead | GroupMessageRead, isGroup: boolean) => void;
  /**
   * Upload a file and attach it to one of MY existing text-only messages
   * (POST /conversations/{id}/media + POST /media/{id}/attach).
   */
  attachMediaToMessage: (input: {
    chatType: ChatType;
    conversationId: number;
    messageId: number;
    file: File;
  }) => Promise<void>;
  markFailed: (clientMessageId: string, reason?: string) => void;
  applyDeliveredWatermark: (input: {
    conversationId: number;
    lastMessageId: number;
    deliveredAt: string;
    myUsername: string;
    isGroup: boolean;
  }) => void;
  applyReadWatermark: (input: {
    conversationId: number;
    lastMessageId: number;
    readAt: string;
    readerUsername: string;
    myUsername: string;
    isGroup: boolean;
  }) => void;
  setTyping: (conversationId: number, username: string, isTyping: boolean) => void;
  pruneTyping: (conversationId: number) => void;
}

function emptyFeed(): ChatFeed {
  return {
    messages: [],
    total: 0,
    oldestLoadedOffset: 0,
    hasOlder: false,
    loading: false,
    loadingOlder: false,
    error: null,
    typing: {},
    lastReadByReader: {},
  };
}

/** Derive the direct-chat tick status from timestamps. */
function directStatusOf(raw: MessageRead): MessageStatus {
  if (raw.read_at) return "read";
  if (raw.delivered_at) return "delivered";
  return "sent";
}

function toChatMessage(
  raw: MessageRead | GroupMessageRead,
  chatType: ChatType
): ChatMessage {
  const isGroup = chatType === "group";
  const group = isGroup ? (raw as GroupMessageRead) : null;
  return {
    id: raw.id,
    clientMessageId: "",
    conversationId: raw.conversation_id,
    chatType,
    senderUsername: raw.sender_username,
    body: raw.body,
    media: raw.media,
    createdAt: raw.created_at,
    deliveredAt: raw.delivered_at,
    readAt: raw.read_at,
    readCount: group?.read_count ?? 0,
    deliveredCount: group?.delivered_count ?? 0,
    memberCount: group?.member_count ?? 0,
    readByMe: group?.read_by_me ?? false,
    status: isGroup
      ? group!.read_count > 0
        ? "read"
        : group!.delivered_count > 0
          ? "delivered"
          : "sent"
      : directStatusOf(raw),
  };
}

function isGroupMessageShape(raw: MessageRead | GroupMessageRead): boolean {
  return "read_count" in raw || "read_by_me" in raw;
}

export const useMessageStore = create<MessageState>()(
  immer((set, get) => ({
    feeds: {},

    loadFeed: async (chatType, conversationId, force = false) => {
      const key = chatKey(chatType, conversationId);
      const existing = get().feeds[key];
      if (existing && !force && (existing.messages.length > 0 || existing.loading)) return;

      set((state) => {
        if (!state.feeds[key]) state.feeds[key] = emptyFeed();
        state.feeds[key].loading = true;
        state.feeds[key].error = null;
      });

      const endpoint =
        chatType === "direct"
          ? `/api/v1/conversations/${conversationId}/messages`
          : `/api/v1/groups/${conversationId}/messages`;

      try {
        // First page = the NEWEST window of history (reverse infinite scroll
        // then pages backwards from here).
        const probe = await apiClient.get<{ items: (MessageRead | GroupMessageRead)[]; total: number }>(
          endpoint,
          { query: { limit: 1, offset: 0 } }
        );
        const total = probe.total;
        const offset = Math.max(0, total - MESSAGE_PAGE_LIMIT);
        const page = await apiClient.get<{ items: (MessageRead | GroupMessageRead)[]; total: number }>(
          endpoint,
          { query: { limit: MESSAGE_PAGE_LIMIT, offset } }
        );

        set((state) => {
          const feed = state.feeds[key];
          feed.messages = page.items.map((raw) =>
            toChatMessage(raw, chatType)
          );
          feed.total = page.total;
          feed.oldestLoadedOffset = offset;
          feed.hasOlder = offset > 0;
          feed.loading = false;
        });
      } catch (error) {
        set((state) => {
          const feed = state.feeds[key];
          feed.loading = false;
          feed.error =
            error instanceof Error ? error.message : "Could not load messages.";
        });
      }
    },

    loadOlder: async (chatType, conversationId) => {
      const key = chatKey(chatType, conversationId);
      const feed = get().feeds[key];
      if (!feed || !feed.hasOlder || feed.loadingOlder) return;

      set((state) => {
        state.feeds[key].loadingOlder = true;
      });

      const endpoint =
        chatType === "direct"
          ? `/api/v1/conversations/${conversationId}/messages`
          : `/api/v1/groups/${conversationId}/messages`;
      const offset = Math.max(0, feed.oldestLoadedOffset - MESSAGE_PAGE_LIMIT);

      try {
        const page = await apiClient.get<{ items: (MessageRead | GroupMessageRead)[]; total: number }>(
          endpoint,
          { query: { limit: MESSAGE_PAGE_LIMIT, offset } }
        );
        set((state) => {
          const target = state.feeds[key];
          const existingIds = new Set(target.messages.map((m) => m.id));
          const older = page.items
            .map((raw) => toChatMessage(raw, chatType))
            .filter((m) => m.id === null || !existingIds.has(m.id))
            .reverse(); // prepend keeps overall oldest-first order
          target.messages.unshift(...older);
          target.oldestLoadedOffset = offset;
          target.hasOlder = offset > 0;
          target.loadingOlder = false;
        });
      } catch {
        set((state) => {
          state.feeds[key].loadingOlder = false;
        });
      }
    },

    sendMessage: async ({ chatType, conversationId, body, mediaId, transport }) => {
      const key = chatKey(chatType, conversationId);
      const clientMessageId = uuidv4();
      const now = new Date().toISOString();

      set((state) => {
        if (!state.feeds[key]) state.feeds[key] = emptyFeed();
        state.feeds[key].messages.push({
          id: null,
          clientMessageId,
          conversationId,
          chatType,
          senderUsername: "__me__",
          body,
          media: null,
          createdAt: now,
          deliveredAt: null,
          readAt: null,
          readCount: 0,
          deliveredCount: 0,
          memberCount: 0,
          readByMe: true,
          status: "pending",
        });
        state.feeds[key].total += 1;
      });

      const accepted = transport({
        conversation_id: conversationId,
        body,
        media_id: mediaId,
        client_message_id: clientMessageId,
      });

      if (!accepted) {
        // Socket down — fall back to the REST endpoint (same validation rules).
        try {
          const endpoint =
            chatType === "direct"
              ? `/api/v1/conversations/${conversationId}/messages`
              : `/api/v1/groups/${conversationId}/messages`;
          const sent = await apiClient.post<MessageRead | GroupMessageRead>(endpoint, {
            body,
            media_id: mediaId,
          });
          get().confirmAck({
            clientMessageId,
            message: sent,
            duplicate: false,
            isGroup: isGroupMessageShape(sent),
          });
          useConversationStore
            .getState()
            .applyIncomingMessage({
              chatType,
              conversationId,
              message: sent,
              countAsUnread: false,
            });
        } catch (error) {
          set((state) => {
            const target = state.feeds[key];
            const pending = target?.messages.find(
              (m) => m.clientMessageId === clientMessageId
            );
            if (pending) pending.status = "failed";
          });
          throw error;
        }
      }
    },

    retryMessage: async ({ chatType, conversationId, clientMessageId, transport }) => {
      const key = chatKey(chatType, conversationId);
      const pending = get().feeds[key]?.messages.find(
        (m) => m.clientMessageId === clientMessageId && m.status === "failed"
      );
      if (!pending) return;

      set((state) => {
        const target = state.feeds[key].messages.find(
          (m) => m.clientMessageId === clientMessageId
        );
        if (target) target.status = "pending";
      });

      const accepted = transport({
        conversation_id: conversationId,
        body: pending.body,
        media_id: pending.media?.id ?? null,
        client_message_id: clientMessageId,
      });

      if (!accepted) {
        try {
          const endpoint =
            chatType === "direct"
              ? `/api/v1/conversations/${conversationId}/messages`
              : `/api/v1/groups/${conversationId}/messages`;
          const sent = await apiClient.post<MessageRead | GroupMessageRead>(endpoint, {
            body: pending.body,
            media_id: pending.media?.id ?? null,
          });
          get().confirmAck({
            clientMessageId,
            message: sent,
            duplicate: false,
            isGroup: isGroupMessageShape(sent),
          });
        } catch {
          set((state) => {
            const target = state.feeds[key].messages.find(
              (m) => m.clientMessageId === clientMessageId
            );
            if (target) target.status = "failed";
          });
        }
      }
    },

    confirmAck: ({ clientMessageId, message, duplicate, isGroup }) =>
      set((state) => {
        const chatType: ChatType = isGroup ? "group" : "direct";
        const key = chatKey(chatType, message.conversation_id);
        const feed = state.feeds[key];
        if (!feed) return;
        const index = feed.messages.findIndex(
          (m) => m.clientMessageId === clientMessageId || m.id === message.id
        );
        if (index < 0) return;
        const normalized = toChatMessage(message, chatType);
        normalized.clientMessageId = clientMessageId;
        feed.messages[index] = normalized;
      }),

    applyIncomingMessage: ({ message, isGroup }) =>
      set((state) => {
        const chatType: ChatType = isGroup ? "group" : "direct";
        const key = chatKey(chatType, message.conversation_id);
        if (!state.feeds[key]) return;
        const feed = state.feeds[key];
        const existingIndex = feed.messages.findIndex((m) => m.id === message.id);
        const normalized = toChatMessage(message, chatType);
        if (existingIndex >= 0) {
          // Same-device race (ack already landed) or history refresh.
          normalized.clientMessageId = feed.messages[existingIndex]!.clientMessageId;
          feed.messages[existingIndex] = normalized;
          return;
        }
        // Replace an optimistic duplicate if the ack has not landed yet.
        const optimisticIndex = feed.messages.findIndex(
          (m) =>
            m.id === null &&
            m.status === "pending" &&
            m.body === message.body &&
            Math.abs(
              new Date(m.createdAt).getTime() - new Date(message.created_at).getTime()
            ) < 15000
        );
        if (optimisticIndex >= 0) {
          feed.messages[optimisticIndex] = normalized;
        } else {
          feed.messages.push(normalized);
        }
        feed.total += 1;
      }),

    applyMessageUpdated: (message, isGroup) =>
      set((state) => {
        const chatType: ChatType = isGroup ? "group" : "direct";
        const key = chatKey(chatType, message.conversation_id);
        const feed = state.feeds[key];
        if (!feed) return;
        const index = feed.messages.findIndex((m) => m.id === message.id);
        if (index >= 0) {
          const normalized = toChatMessage(message, chatType);
          normalized.clientMessageId = feed.messages[index]!.clientMessageId;
          feed.messages[index] = normalized;
        }
      }),

    attachMediaToMessage: async ({ chatType, conversationId, messageId, file }) => {
      const check = isAllowedUpload(file);
      if (!check.ok) throw new Error(check.reason ?? "File type not allowed.");
      if (file.size === 0) throw new Error("Empty files are not allowed.");
      if (file.size > MEDIA_MAX_UPLOAD_BYTES) throw new Error("Files are limited to 10 MB.");

      const key = chatKey(chatType, conversationId);

      // 1) Upload into the conversation's media pool.
      const form = new FormData();
      form.append("files", file);
      const batch = await apiClient.post<{ items: MediaRead[] }>(
        `/api/v1/conversations/${conversationId}/media`,
        undefined,
        { rawBody: form, timeoutMs: MEDIA_UPLOAD_TIMEOUT_MS }
      );
      const media = batch.items[0];
      if (!media) throw new Error("Upload failed.");

      // 2) Attach the uploaded file to the existing message.
      const attached = await apiClient.post<MediaRead>(
        `/api/v1/media/${media.id}/attach`,
        { message_id: messageId }
      );

      // 3) Optimistic local update (the `message_updated` WS echo will
      //    reconcile all other clients).
      set((state) => {
        const feed = state.feeds[key];
        if (!feed) return;
        const target = feed.messages.find((m) => m.id === messageId);
        if (target && !target.media) target.media = attached;
      });
    },

    markFailed: (clientMessageId) =>
      set((state) => {
        for (const feed of Object.values(state.feeds)) {
          const target = feed.messages.find(
            (m) => m.clientMessageId === clientMessageId && m.status === "pending"
          );
          if (target) {
            target.status = "failed";
            return;
          }
        }
      }),

    applyDeliveredWatermark: ({
      conversationId,
      lastMessageId,
      deliveredAt,
      myUsername,
      isGroup,
    }) =>
      set((state) => {
        const chatType: ChatType = isGroup ? "group" : "direct";
        const key = chatKey(chatType, conversationId);
        const feed = state.feeds[key];
        if (!feed) return;
        for (const message of feed.messages) {
          if (message.id === null || message.id > lastMessageId) continue;
          // Ticks describe MY messages' state; the event is only meaningful
          // for the sender (direct: delivered goes to sender devices only).
          if (message.senderUsername !== myUsername) continue;
          if (!message.deliveredAt) {
            message.deliveredAt = deliveredAt;
            if (message.status === "sent") message.status = "delivered";
          } else if (isGroup) {
            message.deliveredCount = Math.min(
              (message.deliveredCount ?? 0) + 1,
              Math.max(0, message.memberCount - 1)
            );
          }
        }
      }),

    applyReadWatermark: ({
      conversationId,
      lastMessageId,
      readAt,
      readerUsername,
      myUsername,
      isGroup,
    }) =>
      set((state) => {
        const chatType: ChatType = isGroup ? "group" : "direct";
        const key = chatKey(chatType, conversationId);
        const feed = state.feeds[key];
        if (!feed) return;

        if (isGroup) {
          // Event = "reader X read everything up to lastMessageId".
          const previous = feed.lastReadByReader[readerUsername] ?? 0;
          if (lastMessageId <= previous) return; // stale replay, no double count
          for (const message of feed.messages) {
            if (message.id === null) continue;
            if (message.id > lastMessageId || message.id <= previous) continue;
            if (message.senderUsername === myUsername) {
              message.readCount = Math.min(
                (message.readCount ?? 0) + 1,
                Math.max(0, message.memberCount - 1)
              );
              message.status = "read";
            } else if (readerUsername === myUsername) {
              message.readByMe = true;
            }
          }
          feed.lastReadByReader[readerUsername] = lastMessageId;
          return;
        }

        // Direct chat: only my own messages gain ticks. My own read echo
        // (reader === me) changes nothing visually in my feed.
        if (readerUsername === myUsername) return;
        for (const message of feed.messages) {
          if (message.id === null || message.id > lastMessageId) continue;
          if (message.senderUsername !== myUsername) continue;
          message.readAt = readAt;
          message.deliveredAt = message.deliveredAt ?? readAt;
          message.status = "read";
        }
      }),

    setTyping: (conversationId, username, isTyping) =>
      set((state) => {
        // Conversation ids are globally unique across the two key spaces.
        const feed =
          state.feeds[`direct:${conversationId}`] ?? state.feeds[`group:${conversationId}`];
        if (!feed) return;
        if (isTyping) {
          feed.typing[username] = Date.now() + TYPING_EXPIRY_MS;
        } else {
          delete feed.typing[username];
        }
      }),

    pruneTyping: (conversationId) =>
      set((state) => {
        const now = Date.now();
        const feed =
          state.feeds[`direct:${conversationId}`] ?? state.feeds[`group:${conversationId}`];
        if (!feed) return;
        for (const [username, expiresAt] of Object.entries(feed.typing)) {
          if (expiresAt <= now) delete feed.typing[username];
        }
      }),
  }))
);

registerStoreReset("messages", () => {
  useMessageStore.setState({ feeds: {} });
});
