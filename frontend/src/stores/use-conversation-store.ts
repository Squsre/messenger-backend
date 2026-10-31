"use client";

/**
 * Conversations & groups: lists, ordering, unread counters, active chat.
 *
 * Ordering rule: most recent activity first — `last_message_at` falling back
 * to `created_at` (identical to the backend list contract).
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type {
  ConversationDetail,
  ConversationPage,
  ConversationRead,
  GroupInvitationRead,
  GroupMessagePreview,
  GroupPage,
  GroupRead,
  MessageRead,
} from "@/types/api";
import { apiClient, ApiError } from "@/lib/api-client";
import {
  CONVERSATIONS_PAGE_LIMIT,
  READY_CATCHUP_MAX_PAGES,
  RECONCILE_MAX_PROBES,
  RECONCILE_RETRY_AFTER_MS,
} from "@/lib/constants";
import { registerStoreReset } from "@/stores/registry";

export type ChatType = "direct" | "group";

export interface ActiveChat {
  type: ChatType;
  id: number;
}

export function chatKey(type: ChatType, id: number): string {
  return `${type}:${id}`;
}

/** Sort key — mirrors the backend "most recent activity" ordering. */
export function activityOf(item: {
  last_message_at: string | null;
  created_at: string;
}): number {
  const iso = item.last_message_at ?? item.created_at;
  const time = new Date(iso).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function sortConversations(list: ConversationRead[]): void {
  list.sort((a, b) => activityOf(b) - activityOf(a));
}

function sortGroups(list: GroupRead[]): void {
  list.sort((a, b) => activityOf(b) - activityOf(a));
}

/**
 * A message frame may arrive for a conversation/group that is not in the
 * lists yet (e.g. right after a reload, when the socket beats the HTTP
 * lists). Dropping it silently loses the unread bump AND the preview, so
 * instead we schedule a debounced background refresh — the server list
 * carries the authoritative unread_count.
 */
const pendingListRefresh = { direct: false, group: false };
let listRefreshTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleListRefresh(): void {
  if (listRefreshTimer) return;
  listRefreshTimer = setTimeout(() => {
    listRefreshTimer = null;
    if (pendingListRefresh.direct) {
      pendingListRefresh.direct = false;
      void useConversationStore.getState().loadConversations(true);
    }
    if (pendingListRefresh.group) {
      pendingListRefresh.group = false;
      void useConversationStore.getState().loadGroups(true);
    }
  }, 300);
}

/* ------------------------- Single-chat detail fetches ------------------------ */

/** In-flight dedupe across viewport deep links and ready reconciliation. */
const detailInflight = new Map<string, Promise<unknown>>();
/** Direct ids that failed both probes recently (id -> epoch ms). */
const deadProbeCache = new Map<number, number>();

function probeDirect(id: number): Promise<ConversationDetail> {
  return apiClient.get<ConversationDetail>(`/api/v1/conversations/${id}`);
}

function probeGroup(id: number): Promise<GroupRead> {
  return apiClient.get<GroupRead>(`/api/v1/groups/${id}`);
}

interface ConversationState {
  conversations: ConversationRead[];
  conversationsLoading: boolean;
  conversationsLoaded: boolean;
  /** Total conversations on the server (from the page metadata). */
  conversationsTotal: number | null;
  /** Offset for the NEXT "load older" page (independent of list length). */
  conversationsNextOffset: number;
  loadingOlderConversations: boolean;
  groups: GroupRead[];
  groupsLoading: boolean;
  groupsLoaded: boolean;
  groupsTotal: number | null;
  groupsNextOffset: number;
  loadingOlderGroups: boolean;
  pendingInvitations: GroupInvitationRead[];
  invitationsLoading: boolean;
  activeChat: ActiveChat | null;

  loadConversations: (force?: boolean) => Promise<void>;
  loadGroups: (force?: boolean) => Promise<void>;
  /**
   * Fetch the next offset page of the chats list and merge it (dedupe by
   * id). Resolves true when the page contributed rows, false when the list
   * was exhausted / already loading / not booted yet.
   */
  loadOlderConversations: () => Promise<boolean>;
  /** Same for the groups list. */
  loadOlderGroups: () => Promise<boolean>;
  /**
   * Busy-account catch-up after `ready`: auto-pull bounded extra pages of
   * BOTH lists until every conversation id from the ready frame is known.
   * Complements reconcileServerIds (which detail-probes at most 12 ids).
   */
  catchUpLists: (ids: number[]) => Promise<void>;
  loadPendingInvitations: (force?: boolean) => Promise<void>;
  /**
   * Deep-link self-healing for /c/{id}: fetch a single conversation that is
   * missing from the (stale, 50-row) list. Returns null when unavailable.
   * The stored row may be the richer detail (message_count) or a plain list
   * row that was already cached.
   */
  resolveDirectChat: (id: number) => Promise<ConversationRead | null>;
  /** Same for /g/{id}. */
  resolveGroupChat: (id: number) => Promise<GroupRead | null>;
  /**
   * Reconcile the conversation id list from the WS `ready` frame: ids the
   * lists do not know (page-2 chats) are probed via the detail endpoints so
   * they appear immediately after every (re)connect.
   */
  reconcileServerIds: (ids: number[]) => Promise<void>;
  openDirectChat: (username: string) => Promise<ConversationDetail>;
  setActiveChat: (chat: ActiveChat | null) => void;
  upsertDirectConversation: (conversation: ConversationRead | ConversationDetail) => void;
  upsertGroup: (group: GroupRead) => void;
  applyGroupPreview: (groupId: number, preview: GroupMessagePreview) => void;
  /** Bump ordering + preview when a message frame arrives. */
  applyIncomingMessage: (input: {
    chatType: ChatType;
    conversationId: number;
    message: MessageRead | GroupMessagePreview;
    /** Should the unread counter grow? (not mine / not currently visible) */
    countAsUnread: boolean;
  }) => void;
  markReadLocally: (chatType: ChatType, conversationId: number, unreadCount?: number) => void;
  applyPresence: (username: string, lastSeenAt: string | null) => void;
  addPendingInvitation: (invitation: GroupInvitationRead) => void;
  removeInvitation: (invitationId: number) => void;
  handleGroupRemoved: (groupId: number) => void;
}

export const useConversationStore = create<ConversationState>()(
  immer((set, get) => ({
    conversations: [],
    conversationsLoading: false,
    conversationsLoaded: false,
    conversationsTotal: null,
    conversationsNextOffset: 0,
    loadingOlderConversations: false,
    groups: [],
    groupsLoading: false,
    groupsLoaded: false,
    groupsTotal: null,
    groupsNextOffset: 0,
    loadingOlderGroups: false,
    pendingInvitations: [],
    invitationsLoading: false,
    activeChat: null,

    loadConversations: async (force = false) => {
      if (get().conversationsLoading) return;
      if (get().conversationsLoaded && !force) return;
      set((state) => {
        state.conversationsLoading = true;
      });
      try {
        const page = await apiClient.get<ConversationPage>(
          "/api/v1/conversations",
          { query: { limit: CONVERSATIONS_PAGE_LIMIT } }
        );
        set((state) => {
          // Preserve rows from previously loaded older pages: a background
          // refresh (unread resync, reconnect) must not collapse the list
          // back to page 1 in front of the user. Rows the fresh page also
          // contains are replaced by the fresh version; server-side deleted
          // ghosts are pruned on the next "load older" cycle instead.
          const freshIds = new Set(page.items.map((c) => c.id));
          const extras = state.conversations.filter((c) => !freshIds.has(c.id));
          state.conversations = [...page.items, ...extras];
          sortConversations(state.conversations);
          state.conversationsLoaded = true;
          state.conversationsTotal = page.total;
          state.conversationsNextOffset = extras.length
            ? Math.max(state.conversationsNextOffset, page.offset + page.items.length)
            : page.offset + page.items.length;
        });
      } finally {
        set((state) => {
          state.conversationsLoading = false;
        });
      }
    },

    loadOlderConversations: async () => {
      const s = get();
      if (!s.conversationsLoaded || s.conversationsLoading) return false;
      if (s.loadingOlderConversations) return false;
      if (
        s.conversationsTotal !== null &&
        s.conversationsNextOffset >= s.conversationsTotal
      )
        return false;
      const offset = s.conversationsNextOffset;
      set((state) => {
        state.loadingOlderConversations = true;
      });
      try {
        const page = await apiClient.get<ConversationPage>(
          "/api/v1/conversations",
          { query: { limit: CONVERSATIONS_PAGE_LIMIT, offset } }
        );
        set((state) => {
          const known = new Set(state.conversations.map((c) => c.id));
          for (const row of page.items) {
            // A ready-probe may have inserted the same chat meanwhile.
            if (!known.has(row.id)) state.conversations.push(row);
          }
          sortConversations(state.conversations);
          state.conversationsTotal = page.total;
          state.conversationsNextOffset = page.offset + page.items.length;
        });
        return page.items.length > 0;
      } catch {
        // Network / rate-limit failure: keep the button available
        // (nextOffset < total still holds) so the user can retry.
        return false;
      } finally {
        set((state) => {
          state.loadingOlderConversations = false;
        });
      }
    },

    loadGroups: async (force = false) => {
      if (get().groupsLoading) return;
      if (get().groupsLoaded && !force) return;
      set((state) => {
        state.groupsLoading = true;
      });
      try {
        const page = await apiClient.get<GroupPage>(
          "/api/v1/groups",
          { query: { limit: CONVERSATIONS_PAGE_LIMIT } }
        );
        set((state) => {
          const freshIds = new Set(page.items.map((g) => g.id));
          const extras = state.groups.filter((g) => !freshIds.has(g.id));
          state.groups = [...page.items, ...extras];
          sortGroups(state.groups);
          state.groupsLoaded = true;
          state.groupsTotal = page.total;
          state.groupsNextOffset = extras.length
            ? Math.max(state.groupsNextOffset, page.offset + page.items.length)
            : page.offset + page.items.length;
        });
      } finally {
        set((state) => {
          state.groupsLoading = false;
        });
      }
    },

    loadOlderGroups: async () => {
      const s = get();
      if (!s.groupsLoaded || s.groupsLoading) return false;
      if (s.loadingOlderGroups) return false;
      if (s.groupsTotal !== null && s.groupsNextOffset >= s.groupsTotal)
        return false;
      const offset = s.groupsNextOffset;
      set((state) => {
        state.loadingOlderGroups = true;
      });
      try {
        const page = await apiClient.get<GroupPage>(
          "/api/v1/groups",
          { query: { limit: CONVERSATIONS_PAGE_LIMIT, offset } }
        );
        set((state) => {
          const known = new Set(state.groups.map((g) => g.id));
          for (const row of page.items) {
            if (!known.has(row.id)) state.groups.push(row);
          }
          sortGroups(state.groups);
          state.groupsTotal = page.total;
          state.groupsNextOffset = page.offset + page.items.length;
        });
        return page.items.length > 0;
      } catch {
        return false;
      } finally {
        set((state) => {
          state.loadingOlderGroups = false;
        });
      }
    },

    catchUpLists: async (ids) => {
      // Skip during boot: the initial lists are loading anyway and every id
      // would look unknown, causing a useless page burst.
      if (!get().conversationsLoaded || !get().groupsLoaded) return;
      for (let round = 0; round < READY_CATCHUP_MAX_PAGES; round += 1) {
        const state = get();
        const known = new Set<number>();
        for (const conversation of state.conversations) known.add(conversation.id);
        for (const group of state.groups) known.add(group.id);
        if (ids.every((id) => known.has(id))) return;
        const [directPage, groupPage] = await Promise.all([
          state.loadOlderConversations(),
          state.loadOlderGroups(),
        ]);
        // Both lists exhausted without covering the ids — the remaining
        // strays are handled by reconcileServerIds' bounded probes.
        if (!directPage && !groupPage) return;
      }
    },

    loadPendingInvitations: async (force = false) => {
      if (get().invitationsLoading && !force) return;
      set((state) => {
        state.invitationsLoading = true;
      });
      try {
        const page = await apiClient.get<{ items: GroupInvitationRead[]; total: number }>(
          "/api/v1/groups/invitations",
          { query: { status: "pending", limit: 50 } }
        );
        set((state) => {
          state.pendingInvitations = page.items;
        });
      } catch {
        set((state) => {
          state.pendingInvitations = [];
        });
      } finally {
        set((state) => {
          state.invitationsLoading = false;
        });
      }
    },

    openDirectChat: async (username) => {
      const detail = await apiClient.post<ConversationDetail>(
        "/api/v1/conversations",
        { username }
      );
      get().upsertDirectConversation(detail);
      return detail;
    },

    resolveDirectChat: async (id) => {
      const cached = get().conversations.find((c) => c.id === id);
      if (cached) return cached;
      const key = `direct:${id}`;
      let inflight = detailInflight.get(key) as Promise<ConversationDetail> | undefined;
      if (!inflight) {
        inflight = probeDirect(id);
        detailInflight.set(key, inflight);
      }
      try {
        const detail = await inflight;
        get().upsertDirectConversation(detail);
        return detail as ConversationRead;
      } catch {
        return null;
      } finally {
        detailInflight.delete(key);
      }
    },

    resolveGroupChat: async (id) => {
      const cached = get().groups.find((g) => g.id === id);
      if (cached) return cached;
      const key = `group:${id}`;
      let inflight = detailInflight.get(key) as Promise<GroupRead> | undefined;
      if (!inflight) {
        inflight = probeGroup(id);
        detailInflight.set(key, inflight);
      }
      try {
        const detail = await inflight;
        // Non-member results are intentionally dropped by upsertGroup; the
        // caller keeps its own copy for the header in that case.
        get().upsertGroup(detail);
        return detail;
      } catch {
        return null;
      } finally {
        detailInflight.delete(key);
      }
    },

    reconcileServerIds: async (ids) => {
      const state = get();
      // Skip during boot: the lists are loading anyway and EVERY id would
      // look unknown, causing a useless probe burst.
      if (!state.conversationsLoaded || !state.groupsLoaded) return;
      const known = new Set<number>();
      for (const conversation of state.conversations) known.add(conversation.id);
      for (const group of state.groups) known.add(group.id);
      const now = Date.now();
      const missing = ids.filter((id) => {
        if (known.has(id)) return false;
        const failedAt = deadProbeCache.get(id);
        return !failedAt || now - failedAt > RECONCILE_RETRY_AFTER_MS;
      });
      if (missing.length === 0) return;
      const batch = missing.slice(0, RECONCILE_MAX_PROBES);
      await Promise.all(
        batch.map(async (id) => {
          // The ready frame carries bare ids without the kind, so probe the
          // (more common) direct detail first and fall back to the group.
          try {
            const detail = await probeDirect(id);
            deadProbeCache.delete(id);
            get().upsertDirectConversation(detail);
          } catch (error) {
            if (error instanceof ApiError && error.status === 404) {
              try {
                const group = await probeGroup(id);
                deadProbeCache.delete(id);
                get().upsertGroup(group);
              } catch {
                deadProbeCache.set(id, Date.now());
              }
            } else {
              // 403 / rate limit / offline — retry on a later reconnect.
              deadProbeCache.set(id, Date.now());
            }
          }
        })
      );
    },

    setActiveChat: (chat) =>
      set((state) => {
        state.activeChat = chat;
      }),

    upsertDirectConversation: (conversation) =>
      set((state) => {
        const index = state.conversations.findIndex((c) => c.id === conversation.id);
        if (index >= 0) {
          state.conversations[index] = conversation;
        } else {
          state.conversations.push(conversation);
        }
        sortConversations(state.conversations);
      }),

    upsertGroup: (group) =>
      set((state) => {
        if (!group.is_member) {
          // Discovery results must not enter "my groups".
          const index = state.groups.findIndex((g) => g.id === group.id);
          if (index >= 0 && !group.is_member) state.groups.splice(index, 1);
          return;
        }
        const index = state.groups.findIndex((g) => g.id === group.id);
        if (index >= 0) {
          state.groups[index] = group;
        } else {
          state.groups.push(group);
        }
        sortGroups(state.groups);
      }),

    applyGroupPreview: (groupId, preview) =>
      set((state) => {
        const group = state.groups.find((g) => g.id === groupId);
        if (!group) return;
        group.last_message = preview;
        group.last_message_at = preview.created_at;
        sortGroups(state.groups);
      }),

    applyIncomingMessage: ({ chatType, conversationId, message, countAsUnread }) =>
      set((state) => {
        const createdAt = message.created_at;
        if (chatType === "direct") {
          const conversation = state.conversations.find((c) => c.id === conversationId);
          if (!conversation) {
            // Not loaded yet — refetch so the unread count is not lost.
            pendingListRefresh.direct = true;
            scheduleListRefresh();
            return;
          }
          conversation.last_message = message as MessageRead;
          conversation.last_message_at = createdAt;
          if (countAsUnread) conversation.unread_count += 1;
          sortConversations(state.conversations);
        } else {
          const group = state.groups.find((g) => g.id === conversationId);
          if (!group) {
            // Not loaded yet — refetch so the unread count is not lost.
            pendingListRefresh.group = true;
            scheduleListRefresh();
            return;
          }
          group.last_message = {
            id: message.id,
            sender_username: message.sender_username,
            body: message.body,
            media: message.media,
            created_at: message.created_at,
          };
          group.last_message_at = createdAt;
          if (countAsUnread) group.unread_count += 1;
          sortGroups(state.groups);
        }
      }),

    markReadLocally: (chatType, conversationId, unreadCount) =>
      set((state) => {
        if (chatType === "direct") {
          const conversation = state.conversations.find((c) => c.id === conversationId);
          if (conversation) conversation.unread_count = unreadCount ?? 0;
        } else {
          const group = state.groups.find((g) => g.id === conversationId);
          if (group) group.unread_count = unreadCount ?? 0;
        }
      }),

    applyPresence: (username, lastSeenAt) =>
      set((state) => {
        for (const conversation of state.conversations) {
          if (conversation.participant.username === username) {
            conversation.participant.last_seen_at = lastSeenAt;
          }
        }
      }),

    addPendingInvitation: (invitation) =>
      set((state) => {
        const exists = state.pendingInvitations.some((i) => i.id === invitation.id);
        if (!exists) state.pendingInvitations.push(invitation);
      }),

    removeInvitation: (invitationId) =>
      set((state) => {
        state.pendingInvitations = state.pendingInvitations.filter(
          (i) => i.id !== invitationId
        );
      }),

    handleGroupRemoved: (groupId) =>
      set((state) => {
        state.groups = state.groups.filter((g) => g.id !== groupId);
        if (state.activeChat?.type === "group" && state.activeChat.id === groupId) {
          state.activeChat = null;
        }
      }),
  }))
);

registerStoreReset("conversations", () => {
  deadProbeCache.clear();
  useConversationStore.setState(
    {
      conversations: [],
      conversationsLoading: false,
      conversationsLoaded: false,
      conversationsTotal: null,
      conversationsNextOffset: 0,
      loadingOlderConversations: false,
      groups: [],
      groupsLoading: false,
      groupsLoaded: false,
      groupsTotal: null,
      groupsNextOffset: 0,
      loadingOlderGroups: false,
      pendingInvitations: [],
      invitationsLoading: false,
      activeChat: null,
    },
    );
});
