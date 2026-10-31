"use client";

import { NewChatDialog } from "@/components/dialogs/new-chat-dialog";
import { CreateGroupDialog } from "@/components/dialogs/create-group-dialog";
import { InvitationsDialog } from "@/components/dialogs/invitations-dialog";
import { DiscoverGroupsDialog } from "@/components/dialogs/discover-groups-dialog";
import { CommandPaletteDialog } from "@/components/dialogs/command-palette-dialog";

/** All app-level dialogs, mounted once and driven by the UI store. */
export function DialogRoot() {
  return (
    <>
      <NewChatDialog />
      <CreateGroupDialog />
      <InvitationsDialog />
      <DiscoverGroupsDialog />
      <CommandPaletteDialog />
    </>
  );
}
