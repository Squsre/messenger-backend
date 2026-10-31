"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown, Command, Compass, BookUser, PanelLeftClose, Search, UsersRound } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { useConversationStore } from "@/stores/use-conversation-store";
import { useAuthStore } from "@/stores/use-auth-store";
import { useUiStore } from "@/stores/use-ui-store";
import { UnverifiedEmailSidebarCard } from "@/components/auth/unverified-email-banner";
import { useTypingUsers } from "@/hooks/use-typing";
import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";
import { UserAvatar, GroupAvatar } from "@/components/common/user-avatar";
import { StatusTick } from "@/components/common/status-tick";
import { EmptyState } from "@/components/common/empty-state";
import {
  displayNameOf,
  formatListTimestamp,
} from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

/* ------------------------------- List items -------------------------------- */

function DirectListItem({
  id,
  username,
  displayName,
  lastMessage,
  lastMessageAt,
  unreadCount,
  active,
}: {
  id: number;
  username: string;
  displayName: string | null;
  lastMessage: { body: string | null; media: { filename: string; content_type: string } | null; sender_username: string; status?: string } | null;
  lastMessageAt: string | null;
  unreadCount: number;
  active: boolean;
}) {
  // Snapshot-safe typing selection (see hooks/use-typing.ts).
  const typing = useTypingUsers("direct", id).length > 0;

  const preview = typing
    ? "typing…"
    : lastMessage
      ? lastMessage.body ??
        (lastMessage.media
          ? lastMessage.media.content_type.startsWith("image/")
            ? "Photo"
            : lastMessage.media.filename
          : "")
      : "No messages yet";

  return (
    <Link
      href={`/c/${id}`}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-accent",
        active && "bg-accent"
      )}
    >
      <UserAvatar name={displayName} username={username} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span dir="auto" className="truncate text-start text-sm font-medium">
            {displayNameOf(displayName, username)}
          </span>
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {formatListTimestamp(lastMessageAt)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span
            dir="auto"
            className={cn(
              "bidi-plaintext truncate text-start text-xs text-muted-foreground",
              typing && "text-primary"
            )}
          >
            {preview}
          </span>
          {unreadCount > 0 ? (
            <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold text-primary-foreground">
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          ) : null}
        </div>
      </div>
    </Link>
  );
}

function GroupListItem({
  id,
  name,
  lastMessage,
  lastMessageAt,
  unreadCount,
  memberCount,
  active,
}: {
  id: number;
  name: string;
  lastMessage: { body: string | null; media: { filename: string; content_type: string } | null; sender_username: string } | null;
  lastMessageAt: string | null;
  unreadCount: number;
  memberCount: number;
  active: boolean;
}) {
  // Snapshot-safe typing selection (see hooks/use-typing.ts).
  const typingUsernames = useTypingUsers("group", id);

  const preview = typingUsernames.length
    ? `${typingUsernames[0]} is typing…`
    : lastMessage
      ? `${lastMessage.sender_username}: ${lastMessage.body ?? (lastMessage.media ? (lastMessage.media.content_type.startsWith("image/") ? "Photo" : lastMessage.media.filename) : "")}`
      : "No messages yet";

  return (
    <Link
      href={`/g/${id}`}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-accent",
        active && "bg-accent"
      )}
    >
      <GroupAvatar name={name} memberCount={memberCount} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span dir="auto" className="truncate text-start text-sm font-medium">{name}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {formatListTimestamp(lastMessageAt)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span
            dir="auto"
            className={cn(
              "bidi-plaintext truncate text-start text-xs text-muted-foreground",
              typingUsernames.length > 0 && "text-primary"
            )}
          >
            {preview}
          </span>
          {unreadCount > 0 ? (
            <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold text-primary-foreground">
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          ) : null}
        </div>
      </div>
    </Link>
  );
}

/* --------------------------------- Sidebar ---------------------------------- */

/**
 * Footer row under a list: "Load older …" with a showing counter. Only
 * rendered while the server total exceeds the loaded window; hidden while
 * a search filter is active (filtered views cover loaded rows only, and a
 * note says so instead of implying exhaustive results).
 */
function LoadOlderRow({
  noun,
  loadedCount,
  total,
  nextOffset,
  loading,
  filtering,
  onLoad,
}: {
  noun: string;
  loadedCount: number;
  total: number | null;
  nextOffset: number;
  loading: boolean;
  filtering: boolean;
  onLoad: () => void;
}) {
  if (total === null || nextOffset >= total) return null;
  return (
    <div className="flex flex-col items-center gap-1 px-2 pb-3 pt-2">
      <Button
        variant="ghost"
        size="sm"
        className="h-8 gap-1.5 text-xs text-muted-foreground"
        disabled={loading}
        onClick={onLoad}
        aria-label={`Load older ${noun}`}
      >
        {loading ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <ChevronDown className="size-3.5" />
        )}
        Load older {noun}
      </Button>
      <p className="text-[11px] text-muted-foreground">
        {filtering
          ? `Search covers the ${loadedCount} loaded of ${total} ${noun} — load older to search further`
          : `Showing ${loadedCount} of ${total} ${noun}`}
      </p>
    </div>
  );
}

export function ChatSidebar() {
  const pathname = usePathname();
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"chats" | "groups">("chats");

  // Email-verification nudge lives above the chat list (account-level).
  const emailVerified = useAuthStore((s) => s.user?.email_verified ?? true);
  const email = useAuthStore((s) => s.user?.email ?? "");

  const conversations = useConversationStore((s) => s.conversations);
  const conversationsLoading = useConversationStore((s) => s.conversationsLoading);
  const conversationsTotal = useConversationStore((s) => s.conversationsTotal);
  const conversationsNextOffset = useConversationStore((s) => s.conversationsNextOffset);
  const loadingOlderConversations = useConversationStore((s) => s.loadingOlderConversations);
  const groups = useConversationStore((s) => s.groups);
  const groupsLoading = useConversationStore((s) => s.groupsLoading);
  const groupsTotal = useConversationStore((s) => s.groupsTotal);
  const groupsNextOffset = useConversationStore((s) => s.groupsNextOffset);
  const loadingOlderGroups = useConversationStore((s) => s.loadingOlderGroups);

  const filteredConversations = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter(
      (c) =>
        c.participant.username.includes(q) ||
        (c.participant.display_name ?? "").toLowerCase().includes(q)
    );
  }, [conversations, query]);

  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter((g) => g.name.toLowerCase().includes(q));
  }, [groups, query]);

  const totalUnread = useMemo(
    () => conversations.reduce((sum, c) => sum + c.unread_count, 0),
    [conversations]
  );
  const groupUnread = useMemo(
    () => groups.reduce((sum, g) => sum + g.unread_count, 0),
    [groups]
  );

  const activeDirectId = pathname.startsWith("/c/")
    ? Number(pathname.split("/")[2])
    : null;
  const activeGroupId = pathname.startsWith("/g/")
    ? Number(pathname.split("/")[2])
    : null;

  return (
    <aside className="flex h-full w-full flex-col border-r bg-background md:w-80 lg:w-96">
      <div className="space-y-3 border-b p-4">
        <div className="flex items-center justify-between gap-2">
          <h1 className="font-brand text-lg font-bold tracking-wide">Messenger</h1>
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground"
              aria-label="Search everything (Ctrl+K)"
              title="Search everything (Ctrl+K)"
              onClick={() => useUiStore.getState().setCommandPaletteOpen(true)}
            >
              <Command className="size-4" />
            </Button>
            <Button
              asChild
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground"
              aria-label="Contacts"
              title="Contacts"
            >
              <Link href="/contacts">
                <BookUser className="size-4" />
              </Link>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground"
              aria-label="Discover public groups"
              title="Discover public groups"
              onClick={() => useUiStore.getState().setDiscoverGroupsOpen(true)}
            >
              <Compass className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground"
              aria-label="Minimize chat list"
              title="Minimize chat list"
              onClick={() => useUiStore.getState().setLeftSidebarCollapsed(true)}
            >
              <PanelLeftClose className="size-4" />
            </Button>
          </div>
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tab === "chats" ? "Search chats" : "Search groups"}
            className="pl-8 h-9"
            aria-label={tab === "chats" ? "Search chats" : "Search groups"}
          />
        </div>
        <Tabs value={tab} onValueChange={(v) => setTab(v as "chats" | "groups")}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="chats" className="gap-1.5">
              Chats
              {totalUnread > 0 && (
                <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground">
                  {totalUnread}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="groups" className="gap-1.5">
              Groups
              {groupUnread > 0 && (
                <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground">
                  {groupUnread}
                </span>
              )}
            </TabsTrigger>
          </TabsList>
        </Tabs>
        {!emailVerified && <UnverifiedEmailSidebarCard email={email} />}
      </div>

      <ScrollArea className="chat-scroll flex-1 min-h-0">
        <div className="space-y-0.5 p-2">
          {tab === "chats" ? (
            conversationsLoading && conversations.length === 0 ? (
              Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 px-3 py-2.5">
                  <Skeleton className="size-10 rounded-full" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-3.5 w-2/3" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                </div>
              ))
            ) : filteredConversations.length === 0 ? (
              <EmptyState
                icon={Search}
                title={query ? "No chats match" : "No conversations yet"}
                description={
                  query
                    ? "Try a different name."
                    : "Start a private chat with someone using the + button on the left rail."
                }
                className="py-12"
              />
            ) : (
              filteredConversations.map((c) => (
                <DirectListItem
                  key={c.id}
                  id={c.id}
                  username={c.participant.username}
                  displayName={c.participant.display_name}
                  lastMessage={c.last_message}
                  lastMessageAt={c.last_message_at}
                  unreadCount={c.unread_count}
                  active={activeDirectId === c.id}
                />
              ))
            )
          ) : groupsLoading && groups.length === 0 ? (
            Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 px-3 py-2.5">
                <Skeleton className="size-10 rounded-lg" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-3.5 w-2/3" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </div>
            ))
          ) : filteredGroups.length === 0 ? (
            <EmptyState
              icon={UsersRound}
              title={query ? "No groups match" : "No groups yet"}
              description={
                query
                  ? "Try a different name."
                  : "Create a group or accept an invitation to get started."
              }
              className="py-12"
            />
          ) : (
            filteredGroups.map((g) => (
              <GroupListItem
                key={g.id}
                id={g.id}
                name={g.name}
                lastMessage={g.last_message}
                lastMessageAt={g.last_message_at}
                unreadCount={g.unread_count}
                memberCount={g.member_count}
                active={activeGroupId === g.id}
              />
            ))
          )}
        </div>
        {tab === "chats" && (
          <LoadOlderRow
            noun="chats"
            loadedCount={conversations.length}
            total={conversationsTotal}
            nextOffset={conversationsNextOffset}
            loading={loadingOlderConversations}
            filtering={query.trim().length > 0}
            onLoad={() =>
              void useConversationStore.getState().loadOlderConversations()
            }
          />
        )}
        {tab === "groups" && (
          <LoadOlderRow
            noun="groups"
            loadedCount={groups.length}
            total={groupsTotal}
            nextOffset={groupsNextOffset}
            loading={loadingOlderGroups}
            filtering={query.trim().length > 0}
            onLoad={() => void useConversationStore.getState().loadOlderGroups()}
          />
        )}
      </ScrollArea>
    </aside>
  );
}
