"use client";

import Link from "next/link";
import { MessagesSquare, Plus, UsersRound, Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/common/empty-state";
import { useUiStore } from "@/stores/use-ui-store";
import { useConversationStore } from "@/stores/use-conversation-store";

export default function EmptyChatPage() {
  const setNewChatOpen = useUiStore((s) => s.setNewChatOpen);
  const setCreateGroupOpen = useUiStore((s) => s.setCreateGroupOpen);
  const pendingInvitations = useConversationStore((s) => s.pendingInvitations.length);
  const setInvitationsOpen = useUiStore((s) => s.setInvitationsOpen);

  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-muted/20 chat-canvas p-6">
      <div className="hidden md:block absolute left-4 top-4 text-xs text-muted-foreground" />
      <EmptyState
        icon={MessagesSquare}
        title="Select a conversation to start chatting"
        description="Your messages are delivered in realtime over WebSocket. Pick a chat on the left, start a new private conversation, or create a group."
        action={
          <div className="mt-2 flex flex-wrap justify-center gap-2">
            <Button onClick={() => setNewChatOpen(true)}>
              <Plus className="size-4" /> New chat
            </Button>
            <Button variant="outline" onClick={() => setCreateGroupOpen(true)}>
              <UsersRound className="size-4" /> New group
            </Button>
            {pendingInvitations > 0 && (
              <Button variant="outline" onClick={() => setInvitationsOpen(true)}>
                <Bell className="size-4" /> Invitations ({pendingInvitations})
              </Button>
            )}
          </div>
        }
      />
      <p className="mt-6 text-xs text-muted-foreground">
        Tip: press the theme button on the left rail to switch between light and dark.{" "}
        <Link href="/settings" className="text-primary hover:underline">
          Settings
        </Link>{" "}
        holds your profile, privacy and devices.
      </p>
    </div>
  );
}
