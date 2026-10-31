"use client";

/**
 * Contacts: the address-book layer on top of `GET/POST /contacts` and
 * `DELETE /contacts/{username}`.
 *
 * The list lives here (not persisted — server is the truth) so both the
 * Contacts page and the profile panels can answer "is X my contact?" and
 * toggle the relationship without refetching.
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { UserPage, UserPublic } from "@/types/api";
import { apiClient, ApiError } from "@/lib/api-client";
import { CONTACTS_PAGE_LIMIT } from "@/lib/constants";
import { registerStoreReset } from "@/stores/registry";

interface ContactsState {
  contacts: UserPublic[];
  loaded: boolean;
  loading: boolean;
  /** Total contacts on the server (page metadata) — null until first load. */
  total: number | null;
  /** Offset for the next page (independent of list length). */
  nextOffset: number;
  loadingMore: boolean;
  /** username currently being mutated (add or remove). */
  busyUsername: string | null;

  load: (force?: boolean) => Promise<void>;
  /** Fetch the next offset page and merge (dedupe by username). */
  loadMore: () => Promise<void>;
  add: (username: string) => Promise<UserPublic>;
  remove: (username: string) => Promise<void>;
  isContact: (username: string) => boolean;
}

export const useContactsStore = create<ContactsState>()(
  immer((set, get) => ({
    contacts: [],
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
          "/api/v1/contacts",
          { query: { limit: CONTACTS_PAGE_LIMIT } }
        );
        set((state) => {
          state.contacts = page.items;
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
          "/api/v1/contacts",
          { query: { limit: CONTACTS_PAGE_LIMIT, offset } }
        );
        set((state) => {
          const known = new Set(state.contacts.map((c) => c.username));
          for (const row of page.items) {
            if (!known.has(row.username)) state.contacts.push(row);
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

    add: async (username) => {
      set((state) => {
        state.busyUsername = username;
      });
      try {
        const user = await apiClient.post<UserPublic>("/api/v1/contacts", {
          username,
        });
        set((state) => {
          if (!state.contacts.some((c) => c.username === user.username)) {
            state.contacts.push(user);
            // The added row counts against the server total.
            if (state.total !== null) state.total += 1;
          }
          state.loaded = true;
        });
        return user;
      } finally {
        set((state) => {
          state.busyUsername = null;
        });
      }
    },

    remove: async (username) => {
      set((state) => {
        state.busyUsername = username;
      });
      try {
        await apiClient.delete(`/api/v1/contacts/${username}`);
        set((state) => {
          state.contacts = state.contacts.filter((c) => c.username !== username);
          if (state.total !== null) state.total = Math.max(0, state.total - 1);
        });
      } finally {
        set((state) => {
          state.busyUsername = null;
        });
      }
    },

    isContact: (username) => get().contacts.some((c) => c.username === username),
  }))
);

registerStoreReset("contacts", () => {
  useContactsStore.setState({
    contacts: [],
    loaded: false,
    loading: false,
    total: null,
    nextOffset: 0,
    loadingMore: false,
    busyUsername: null,
  });
});

/** Human message for a failed contact mutation (409/403/404…). */
export function contactErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}
