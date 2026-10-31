"use client";

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import { registerStoreReset } from "@/stores/registry";

export type RightSidebarView =
  | "chat-info"
  | "group-members"
  | "user-profile"
  | null;

interface UiState {
  /** New direct chat dialog. */
  newChatOpen: boolean;
  /** Create group dialog. */
  createGroupOpen: boolean;
  /** Pending group invitations dialog. */
  invitationsOpen: boolean;
  /** Discover / join public groups dialog. */
  discoverGroupsOpen: boolean;
  /** Global Ctrl/Cmd+K command palette. */
  commandPaletteOpen: boolean;
  /** Right drawer on desktop; sheet on mobile. */
  rightSidebar: RightSidebarView;
  /** Username shown by the "user-profile" right sidebar view. */
  profileUsername: string | null;
  toggleRightSidebar: (view?: Exclude<RightSidebarView, null>) => void;
  closeRightSidebar: () => void;
  /** Minimized chat-list column (a slim rail with an expand button remains). */
  leftSidebarCollapsed: boolean;
  toggleLeftSidebar: () => void;
  setLeftSidebarCollapsed: (collapsed: boolean) => void;
  setNewChatOpen: (open: boolean) => void;
  setCreateGroupOpen: (open: boolean) => void;
  setInvitationsOpen: (open: boolean) => void;
  setDiscoverGroupsOpen: (open: boolean) => void;
  setCommandPaletteOpen: (open: boolean) => void;
  /** Toggle the command palette (used by the global Ctrl/Cmd+K handler). */
  toggleCommandPalette: () => void;
  /** Open the right drawer with a public profile fetched by username. */
  openUserProfile: (username: string) => void;
  /** Set by auth pages to prefill the email after a redirect. */
  authEmailHint: string | null;
  setAuthEmailHint: (email: string | null) => void;
}

const initial = {
  newChatOpen: false,
  createGroupOpen: false,
  invitationsOpen: false,
  discoverGroupsOpen: false,
  commandPaletteOpen: false,
  rightSidebar: null,
  profileUsername: null,
  leftSidebarCollapsed: false,
  authEmailHint: null,
};

export const useUiStore = create<UiState>()(
  immer((set) => ({
    ...initial,
    toggleRightSidebar: (view = "chat-info") =>
      set((state) => {
        state.rightSidebar = state.rightSidebar === view ? null : view;
      }),
    closeRightSidebar: () =>
      set((state) => {
        state.rightSidebar = null;
      }),
    toggleLeftSidebar: () =>
      set((state) => {
        state.leftSidebarCollapsed = !state.leftSidebarCollapsed;
      }),
    setLeftSidebarCollapsed: (collapsed) =>
      set((state) => {
        state.leftSidebarCollapsed = collapsed;
      }),
    setNewChatOpen: (open) =>
      set((state) => {
        state.newChatOpen = open;
      }),
    setCreateGroupOpen: (open) =>
      set((state) => {
        state.createGroupOpen = open;
      }),
    setInvitationsOpen: (open) =>
      set((state) => {
        state.invitationsOpen = open;
      }),
    setDiscoverGroupsOpen: (open) =>
      set((state) => {
        state.discoverGroupsOpen = open;
      }),
    setCommandPaletteOpen: (open) =>
      set((state) => {
        state.commandPaletteOpen = open;
      }),
    toggleCommandPalette: () =>
      set((state) => {
        state.commandPaletteOpen = !state.commandPaletteOpen;
      }),
    openUserProfile: (username) =>
      set((state) => {
        state.rightSidebar = "user-profile";
        state.profileUsername = username;
      }),
    setAuthEmailHint: (email) =>
      set((state) => {
        state.authEmailHint = email;
      }),
  }))
);

registerStoreReset("ui", () => useUiStore.setState(initial));
