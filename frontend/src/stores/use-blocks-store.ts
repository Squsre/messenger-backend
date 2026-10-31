"use client";

/**
 * Blocklist: the symmetric-block layer on top of `POST/GET /blocks` and
 * `DELETE /blocks/{username}`.
 *
 * A block is stored directionally but applied SYMMETRICALLY by the backend:
 * once it exists neither side can message, contact-add or find the other in
 * search (404 in search, 403 on contacts/messages). Keeping the list in a
 * store lets every surface — the info sidebar, the chat header menu, the
 * composer and Settings → Privacy — answer "is X blocked?" instantly and
 * keep destructive actions guarded by the same source of truth.
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { UserPage, UserPublic } from "@/types/api";
import { apiClient, ApiError } from "@/lib/api-client";
import { CONTACTS_PAGE_LIMIT } from "@/lib/constants";
import { registerStoreReset } from "@/stores/registry";

interface BlocksState {
  blocked: UserPublic[];
  loaded: boolean;
  loading: boolean;
  /** Total blocked users on the server (page metadata) — null until first load. */
  total: number | null;
  /** Offset for the next page (independent of list length). */
  nextOffset: number;
  loadingMore: boolean;
  /** username currently being mutated (block or unblock). */
  busyUsername: string | null;

  load: (force?: boolean) => Promise<void>;
  /** Fetch the next offset page and merge (dedupe by username). */
  loadMore: () => Promise<void>;
  block: (username: string) => Promise<UserPublic>;
  unblock: (username: string) => Promise<void>;
  isBlocked: (username: string) => boolean;
}

export const useBlocksStore = create<BlocksState>()(
  immer((set, get) => ({
    blocked: [],
    loaded: false,
    loading: false,
    total: null,
    nextOffset: 0,
    loadingMore: false,
    busyUsername: null,

    load: async (force = false) => {
      if (get().loading) return;
      if (get().loaded && !force) return;
      set((state) => {
        state.loading = true;
      });
      try {
        const page = await apiClient.get<UserPage>(
          "/api/v1/blocks",
          { query: { limit: CONTACTS_PAGE_LIMIT } }
        );
        set((state) => {
          state.blocked = page.items;
          state.total = page.total;
          state.nextOffset = page.offset + page.items.length;
          state.loaded = true;
        });
      } finally {
        set((state) => {
          state.loading = false;
        });
      }
    },

    loadMore: async () => {
      const s = get();
      if (!s.loaded || s.loading || s.loadingMore) return;
      if (s.total !== null && s.nextOffset >= s.total) return;
      const offset = s.nextOffset;
      set((state) => {
        state.loadingMore = true;
      });
      try {
        const page = await apiClient.get<UserPage>(
          "/api/v1/blocks",
          { query: { limit: CONTACTS_PAGE_LIMIT, offset } }
        );
        set((state) => {
          const known = new Set(state.blocked.map((u) => u.username));
          for (const row of page.items) {
            if (!known.has(row.username)) state.blocked.push(row);
          }
          state.total = page.total;
          state.nextOffset = page.offset + page.items.length;
        });
      } finally {
        set((state) => {
          state.loadingMore = false;
        });
      }
    },

    block: async (username) => {
      set((state) => {
        state.busyUsername = username;
      });
      try {
        try {
          const user = await apiClient.post<UserPublic>("/api/v1/blocks", {
            username,
          });
          set((state) => {
            if (!state.blocked.some((u) => u.username === user.username)) {
              state.blocked.push(user);
              if (state.total !== null) state.total += 1;
            }
            state.loaded = true;
          });
          return user;
        } catch (error) {
          // 409 = the block already exists (e.g. another device blocked in
          // parallel). The user's intent is already satisfied: converge the
          // local state and resolve normally instead of surfacing an error.
          if (error instanceof ApiError && error.status === 409) {
            set((state) => {
              if (!state.blocked.some((u) => u.username === username)) {
                state.blocked.push({
                  username,
                  display_name: username,
                  bio: null,
                  avatar_key: null,
                  profile_visibility: "private",
                  created_at: new Date(0).toISOString(),
                  last_seen_at: null,
                });
                if (state.total !== null) state.total += 1;
              }
              state.loaded = true;
            });
            return {
              username,
              display_name: username,
              bio: null,
              avatar_key: null,
              profile_visibility: "private",
              created_at: new Date(0).toISOString(),
              last_seen_at: null,
            };
          }
          throw error;
        }
      } finally {
        set((state) => {
          state.busyUsername = null;
        });
      }
    },

    unblock: async (username) => {
      set((state) => {
        state.busyUsername = username;
      });
      try {
        await apiClient.delete(`/api/v1/blocks/${username}`);
        set((state) => {
          state.blocked = state.blocked.filter((u) => u.username !== username);
          if (state.total !== null) state.total = Math.max(0, state.total - 1);
        });
      } finally {
        set((state) => {
          state.busyUsername = null;
        });
      }
    },

    isBlocked: (username) => get().blocked.some((u) => u.username === username),
  }))
);

registerStoreReset("blocks", () => {
  useBlocksStore.setState({
    blocked: [],
    loaded: false,
    loading: false,
    total: null,
    nextOffset: 0,
    loadingMore: false,
    busyUsername: null,
  });
});
