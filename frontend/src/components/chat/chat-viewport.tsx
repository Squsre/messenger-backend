"use client";

/**
 * Shared chat viewport for direct (/c/[id]) and group (/g/[id]) routes.
 *
 * Deep links (notifications, contacts page, shared links) are self-healing:
 * when the chat is missing from the 50-row lists, the single-chat detail
 * endpoints (GET /conversations/{id} / GET /groups/{id}) resolve it once
 * before the "unavailable" state is shown.
 *
 * The header + message history form one large drop zone: files dragged
 * anywhere over the chat surface publish to the media drop bus and the
 * composer picks them up (validation, upload, chips) from there.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import {
  ArrowLeft,
  Ban,
  FileUp,
  Info,
  Loader2,
  MoreVertical,
  ShieldCheck,
  UserMinus,
  UserPlus,
  UserRound,
  UsersRound,
} from "lucide-react";
import { publishDroppedFiles } from "@/lib/media-drop-bus";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { MessageFeed } from "@/components/chat/message-feed";
import { ChatInput } from "@/components/chat/chat-input";
import { UserAvatar, GroupAvatar } from "@/components/common/user-avatar";
import { EmptyState } from "@/components/common/empty-state";
import {
  useConversationStore,
  type ChatType,
} from "@/stores/use-conversation-store";
import { useMessageStore } from "@/stores/use-message-store";
import { useUiStore } from "@/stores/use-ui-store";
import { useAuthStore } from "@/stores/use-auth-store";
import { useContactsStore } from "@/stores/use-contacts-store";
import { useBlocksStore } from "@/stores/use-blocks-store";
import { useTypingUsers } from "@/hooks/use-typing";
import { ApiError } from "@/lib/api-client";
import {
  displayNameOf,
  formatLastSeen,
} from "@/lib/utils";
import type { ConversationRead, GroupRead } from "@/types/api";

interface ChatViewportProps {
  chatType: ChatType;
  chatId: number;
}

export function ChatViewport({ chatType, chatId }: ChatViewportProps) {
  // Snapshot-safe existence checks (booleans, never fresh arrays/objects).
  const hasDirect = useConversationStore(
    (s) => chatType === "direct" && s.conversations.some((c) => c.id === chatId)
  );
  const hasGroup = useConversationStore(
    (s) => chatType === "group" && s.groups.some((g) => g.id === chatId)
  );
  const conversation = useConversationStore((s) =>
    chatType === "direct" ? s.conversations.find((c) => c.id === chatId) : undefined
  );
  const group = useConversationStore((s) =>
    chatType === "group" ? s.groups.find((g) => g.id === chatId) : undefined
  );
  const loaded = useConversationStore((s) =>
    chatType === "direct" ? s.conversationsLoaded : s.groupsLoaded
  );
  const setActiveChat = useConversationStore((s) => s.setActiveChat);
  const toggleRightSidebar = useUiStore((s) => s.toggleRightSidebar);
  const myUsername = useAuthStore((s) => s.user?.username ?? "");

  // Fallback copies for chats the detail fetch resolved but the store list
  // does not keep (a public group the user is not a member of, for example).
  const [resolvedConversation, setResolvedConversation] =
    useState<ConversationRead | null>(null);
  const [resolvedGroup, setResolvedGroup] = useState<GroupRead | null>(null);
  const [resolveStatus, setResolveStatus] = useState<"idle" | "loading" | "failed">(
    "idle"
  );
  /** One attempt per (type, id) pair per mount — prevents retry loops. */
  const attemptedKey = useRef<string | null>(null);

  // Chat-area drag & drop: a counter (not a boolean) keeps the overlay
  // stable while the pointer crosses child elements (dragenter/leave fire
  // in pairs on every boundary).
  const [dropDepth, setDropDepth] = useState(0);
  const draggingFiles = dropDepth > 0;

  const hasChat = chatType === "direct" ? hasDirect : hasGroup;

  // Track the active chat for unread counting + read watermarks.
  useEffect(() => {
    setActiveChat({ type: chatType, id: chatId });
    return () => setActiveChat(null);
  }, [chatType, chatId, setActiveChat]);

  // Load the feed for this chat.
  useEffect(() => {
    void useMessageStore.getState().loadFeed(chatType, chatId);
  }, [chatType, chatId]);

  // Reset per-chat resolution state when navigating between chats.
  // MUST run before the attempt effect below (declaration order).
  useEffect(() => {
    setResolvedConversation(null);
    setResolvedGroup(null);
    setResolveStatus("idle");
    attemptedKey.current = null;
  }, [chatType, chatId]);

  // Deep-link self-healing: resolve a chat missing from the lists exactly
  // once, then fall back to the local copy / the unavailable state.
  useEffect(() => {
    if (!loaded || hasChat) return;
    const key = `${chatType}:${chatId}`;
    if (attemptedKey.current === key) return;
    attemptedKey.current = key;
    setResolveStatus("loading");
    const store = useConversationStore.getState();
    const request =
      chatType === "direct"
        ? store.resolveDirectChat(chatId)
        : store.resolveGroupChat(chatId);
    void request
      .then((detail) => {
        if (detail && chatType === "direct") {
          setResolvedConversation(detail);
        }
        if (detail && chatType === "group") {
          setResolvedGroup(detail as GroupRead);
        }
        setResolveStatus(detail ? "idle" : "failed");
      })
      .catch(() => setResolveStatus("failed"));
  }, [loaded, hasChat, chatType, chatId]);

  // Snapshot-safe typing selector (see hooks/use-typing.ts).
  const typingUsernames = useTypingUsers(chatType, chatId, {
    excludeUsername: myUsername,
  });

  const directSource = conversation ?? resolvedConversation;
  const groupSource = group ?? resolvedGroup;

  const header = useMemo(() => {
    if (chatType === "direct" && directSource) {
      const lastSeen = formatLastSeen(directSource.participant.last_seen_at);
      const recent =
        directSource.participant.last_seen_at &&
        Date.now() - new Date(directSource.participant.last_seen_at).getTime() < 90_000;
      return {
        title: displayNameOf(
          directSource.participant.display_name,
          directSource.participant.username
        ),
        subtitle: typingUsernames.length > 0 ? "typing…" : lastSeen ?? " ",
        presence: Boolean(recent),
        username: directSource.participant.username,
        avatar: (
          <UserAvatar
            name={directSource.participant.display_name}
            username={directSource.participant.username}
            showPresence
            presenceOnline={Boolean(recent)}
          />
        ),
      };
    }
    if (chatType === "group" && groupSource) {
      return {
        title: groupSource.name,
        subtitle:
          typingUsernames.length > 0
            ? "typing…"
            : `${groupSource.member_count} member${groupSource.member_count === 1 ? "" : "s"} · ${groupSource.visibility}`,
        presence: false,
        username: groupSource.name,
        avatar: (
          <GroupAvatar name={groupSource.name} memberCount={groupSource.member_count} />
        ),
      };
    }
    return null;
  }, [chatType, directSource, groupSource, typingUsernames.length]);

  if (!header) {
    // Lists may still be loading; a deep-link detail fetch may be in flight.
    if (!loaded || resolveStatus === "loading") {
      if (loaded && resolveStatus === "loading") {
        return (
          <div className="flex flex-1 items-center justify-center bg-chat">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        );
      }
      return <div className="flex-1 bg-chat" />;
    }
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <EmptyState
          icon={UsersRound}
          title="Conversation unavailable"
          description="It may have been deleted, or you might not be a member anymore."
          action={
            <Button asChild variant="outline">
              <Link href="/">Back to chats</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div
      className="relative flex h-full min-h-0 flex-col"
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDropDepth((d) => d + 1);
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        setDropDepth((d) => Math.max(0, d - 1));
      }}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setDropDepth(0);
        publishDroppedFiles(Array.from(e.dataTransfer.files));
      }}
    >
      {draggingFiles && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center border-2 border-dashed border-primary bg-background/70 backdrop-blur-[2px]"
        >
          <div className="flex items-center gap-3 rounded-2xl border bg-background/95 px-6 py-5 shadow-lg">
            <FileUp className="size-7 text-primary" />
            <div>
              <p className="text-sm font-semibold">Drop files to attach</p>
              <p className="text-xs text-muted-foreground">
                Up to 4 files · 10 MB each · JPEG, PNG, GIF, WebP, PDF, TXT, CSV
              </p>
            </div>
          </div>
        </div>
      )}
      <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background px-4">
        <Button
          asChild
          variant="ghost"
          size="icon"
          className="md:hidden"
          aria-label="Back to chats"
        >
          <Link href="/">
            <ArrowLeft className="size-5" />
          </Link>
        </Button>
        <button
          type="button"
          onClick={() => toggleRightSidebar(chatType === "group" ? "group-members" : "chat-info")}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-lg px-1 py-1 text-start transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Open ${chatType === "group" ? "group details" : "profile"}`}
        >
          {header.avatar}
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold leading-tight">
              {header.title}
            </h2>
            <p
              className={`truncate text-xs ${
                typingUsernames.length > 0 ? "text-primary" : "text-muted-foreground"
              }`}
            >
              {header.subtitle}
            </p>
          </div>
        </button>
        {chatType === "direct" && directSource && (
          <DirectHeaderMenu username={directSource.participant.username} />
        )}
        <Button
          variant="ghost"
          size="icon"
          aria-label="Toggle details"
          onClick={() => toggleRightSidebar(chatType === "group" ? "group-members" : "chat-info")}
        >
          <Info className="size-5" />
        </Button>
      </header>

      <MessageFeed chatType={chatType} conversationId={chatId} />
      <ChatInput chatType={chatType} conversationId={chatId} />
    </div>
  );
}

/* ------------------------- Direct chat header menu -------------------------- */

/**
 * Chat-header context menu for direct conversations: quick access to the
 * public profile, the contact toggle and the (guarded) block action —
 * POST /blocks + DELETE /blocks/{username} through the shared blocks store,
 * so the drawer badge and the composer gate update instantly.
 */
function DirectHeaderMenu({ username }: { username: string }) {
  const openUserProfile = useUiStore((s) => s.openUserProfile);
  const isContact = useContactsStore((s) =>
    s.contacts.some((c) => c.username === username)
  );
  const contactBusy = useContactsStore((s) => s.busyUsername === username);
  const isBlocked = useBlocksStore((s) =>
    s.blocked.some((u) => u.username === username)
  );
  const blockBusy = useBlocksStore((s) => s.busyUsername === username);
  const [menuBusy, setMenuBusy] = useState(false);
  /** The destructive block action confirms before its endpoint call fires. */
  const [confirmBlock, setConfirmBlock] = useState(false);

  // Warm both relationship lists so the menu shows the true state.
  useEffect(() => {
    void useContactsStore.getState().load();
    void useBlocksStore.getState().load();
  }, []);

  async function toggleContact() {
    setMenuBusy(true);
    try {
      if (isContact) {
        await useContactsStore.getState().remove(username);
        toast.info(`@${username} removed from contacts`);
      } else {
        await useContactsStore.getState().add(username);
        toast.success(`@${username} added to contacts`);
      }
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The action failed."
      );
    } finally {
      setMenuBusy(false);
    }
  }

  async function unblockUser() {
    setMenuBusy(true);
    try {
      await useBlocksStore.getState().unblock(username);
      toast.info(`Unblocked @${username}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The action failed."
      );
    } finally {
      setMenuBusy(false);
    }
  }

  async function blockUser() {
    setMenuBusy(true);
    try {
      await useBlocksStore.getState().block(username);
      toast.success(`Blocked @${username}`);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "The action failed."
      );
    } finally {
      setMenuBusy(false);
      setConfirmBlock(false);
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Conversation menu">
            <MoreVertical className="size-5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuItem onClick={() => openUserProfile(username)}>
            <UserRound className="size-4" /> View profile
          </DropdownMenuItem>
          {!isBlocked && (
            <DropdownMenuItem
              onClick={() => void toggleContact()}
              disabled={menuBusy || contactBusy}
            >
              {isContact ? (
                <UserMinus className="size-4" />
              ) : (
                <UserPlus className="size-4" />
              )}
              {isContact ? "Remove from contacts" : "Add to contacts"}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          {isBlocked ? (
            <DropdownMenuItem
              onClick={() => void unblockUser()}
              disabled={menuBusy || blockBusy}
            >
              <ShieldCheck className="size-4" /> Unblock user
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem
              variant="destructive"
              onClick={() => setConfirmBlock(true)}
            >
              <Ban className="size-4" /> Block user…
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Destructive guard: blocking stops new messages in both directions. */}
      <AlertDialog
        open={confirmBlock}
        onOpenChange={(next) => {
          if (!next) setConfirmBlock(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Block @{username}?</AlertDialogTitle>
            <AlertDialogDescription>
              Blocking stops new messages in both directions and hides each of
              you from the other&apos;s search results and contact adds. History
              stays readable; you can unblock at any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={menuBusy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={menuBusy}
              onClick={(event) => {
                event.preventDefault();
                void blockUser();
              }}
              className="bg-destructive text-white hover:bg-destructive/90"
            >
              {menuBusy && <Loader2 className="mr-1 size-4 animate-spin" />}
              Block user
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
