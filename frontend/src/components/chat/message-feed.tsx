"use client";

/**
 * MessageFeed: chronological list (oldest first) with
 * - sticky auto-scroll to the newest message,
 * - reverse infinite scroll (older history loads when scrolling up),
 * - day separators,
 * - an "Unread messages" divider anchored at the first incoming message
 *   that was unread on entry, with the viewport opened there instead of
 *   at the very bottom,
 * - bubble grouping for consecutive messages from the same sender
 *   (tighter vertical gap + seam corners between neighbours),
 * - automatic read-watermark advancement while pinned to the bottom,
 * - a floating scroll-to-bottom FAB (past 300px) carrying the unread
 *   count badge; clicking it jumps down and marks the chat as read.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MessageItem } from "@/components/chat/message-item";
import { TypingIndicator } from "@/components/chat/typing-indicator";
import { useMessageStore } from "@/stores/use-message-store";
import type { ChatMessage } from "@/stores/use-message-store";
import { useSocketStore } from "@/stores/use-socket-store";
import { useAuthStore } from "@/stores/use-auth-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import type { ChatType } from "@/stores/use-conversation-store";
import { cn, formatDayDivider } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

interface MessageFeedProps {
  chatType: ChatType;
  conversationId: number;
}

const BOTTOM_EPSILON = 140;
/** Show the scroll-to-bottom FAB beyond this distance from the newest message. */
const FAB_TRIGGER_DISTANCE = 300;
/** Consecutive bubbles from the same sender merge within this window. */
const GROUPING_WINDOW_MS = 5 * 60 * 1000;
/** Gap left above the first unread message when jumping to it. */
const UNREAD_TOP_OFFSET = 84;

/**
 * Locate the FIRST unread incoming message inside the loaded window.
 *
 * The read watermark is monotonic, so unread incoming messages always form
 * a suffix of the incoming sequence: walking backwards and counting the
 * `unread_count`-th incoming message lands exactly on the boundary the
 * server would draw. Returns the message id (null when the unread block
 * extends beyond the loaded window — the fallback then stays bottom-pinned).
 */
function findFirstUnreadId(
  messages: ChatMessage[],
  unreadCount: number,
  myUsername: string
): number | null {
  if (unreadCount <= 0) return null;
  let remaining = unreadCount;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const sender = messages[i].senderUsername;
    if (sender === myUsername || sender === "__me__") continue;
    remaining -= 1;
    if (remaining === 0) return messages[i].id;
  }
  return null;
}

export function MessageFeed({ chatType, conversationId }: MessageFeedProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const prevCountRef = useRef(0);
  const prevScrollHeightRef = useRef(0);
  const [showJump, setShowJump] = useState(false);
  /** Server id of the first unread incoming message at entry (divider). */
  const [unreadAnchorId, setUnreadAnchorId] = useState<number | null>(null);
  /** One unread-anchor capture per conversation mount. */
  const anchorInitRef = useRef<string | null>(null);
  /** Tells the first-load effect to open at the divider instead of the bottom. */
  const skipBottomScrollRef = useRef(false);

  const feed = useMessageStore((s) => s.feeds[`${chatType}:${conversationId}`]);
  const myUsername = useAuthStore((s) => s.user?.username ?? "");
  // Server-side unread counter for the active chat (number — snapshot-safe).
  const unreadCount = useConversationStore((s) =>
    chatType === "direct"
      ? s.conversations.find((c) => c.id === conversationId)?.unread_count ?? 0
      : s.groups.find((g) => g.id === conversationId)?.unread_count ?? 0
  );

  const messages = feed?.messages ?? [];
  const lastMessage = messages[messages.length - 1];
  const lastMessageId = lastMessage?.id ?? null;
  const feedReady = Boolean(feed) && !feed.loading;

  /* ---------------------- Unread divider bookkeeping ----------------------- */

  // Reset per-conversation UI state. Declared BEFORE the capture effect so a
  // conversation switch always re-derives the anchor from the NEW feed.
  useEffect(() => {
    setUnreadAnchorId(null);
    anchorInitRef.current = null;
    skipBottomScrollRef.current = false;
    // A cached feed renders instantly on switch: pin it to the newest
    // message — unless the conversation still has unread entries, in which
    // case the anchor below opens it at the divider instead.
    const feedState =
      useMessageStore.getState().feeds[`${chatType}:${conversationId}`];
    const convState = useConversationStore.getState();
    const unread =
      chatType === "direct"
        ? convState.conversations.find((c) => c.id === conversationId)?.unread_count ?? 0
        : convState.groups.find((g) => g.id === conversationId)?.unread_count ?? 0;
    if (unread === 0 && feedState && feedState.messages.length > 0 && !feedState.loading) {
      requestAnimationFrame(() => {
        const el = containerRef.current;
        if (el) el.scrollTop = el.scrollHeight;
        atBottomRef.current = true;
      });
    }
  }, [chatType, conversationId]);

  // Capture the unread boundary once per conversation, after the first page
  // of history is loaded. The divider stays until the user jumps to latest
  // or switches chats (it is a reading aid, not live state).
  useEffect(() => {
    const convKey = `${chatType}:${conversationId}`;
    if (!feedReady) return;
    if (anchorInitRef.current === convKey) return;
    anchorInitRef.current = convKey;
    const anchorId = findFirstUnreadId(messages, unreadCount, myUsername);
    if (anchorId !== null) {
      skipBottomScrollRef.current = true;
      setUnreadAnchorId(anchorId);
    }
  }, [feedReady, chatType, conversationId, messages, unreadCount, myUsername]);

  // Open the viewport at the first unread message (near the top edge).
  useEffect(() => {
    if (unreadAnchorId === null) return;
    const el = containerRef.current;
    if (!el) return;
    const target = el.querySelector<HTMLDivElement>(`[data-mid="${unreadAnchorId}"]`);
    if (!target) return;
    el.scrollTo({ top: Math.max(0, target.offsetTop - UNREAD_TOP_OFFSET), behavior: "auto" });
  }, [unreadAnchorId]);

  const typingUsernames = useMemo(() => {
    const typing = feed?.typing ?? {};
    const now = Date.now();
    return Object.keys(typing).filter((u) => typing[u] > now);
  }, [feed?.typing]);

  /* ------------------------------ Scroll logic ------------------------------ */

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
  }, []);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const isAtBottom = distance < BOTTOM_EPSILON;
    atBottomRef.current = isAtBottom;
    setShowJump(!isAtBottom && distance > FAB_TRIGGER_DISTANCE);

    if (
      el.scrollTop < 220 &&
      feed &&
      feed.hasOlder &&
      !feed.loadingOlder
    ) {
      prevScrollHeightRef.current = el.scrollHeight;
      void useMessageStore.getState().loadOlder(chatType, conversationId);
    }
  }, [chatType, conversationId, feed]);

  // New messages: stick to the bottom when already there. The very first
  // page defers to the unread anchor (divider) when one was captured.
  useEffect(() => {
    const count = messages.length;
    const previous = prevCountRef.current;
    prevCountRef.current = count;
    if (count === previous) return;
    if (previous === 0) {
      if (skipBottomScrollRef.current) {
        skipBottomScrollRef.current = false;
        return;
      }
      requestAnimationFrame(() => scrollToBottom("auto"));
    } else if (atBottomRef.current) {
      requestAnimationFrame(() => scrollToBottom(count - previous > 5 ? "auto" : "smooth"));
    } else if (count > previous) {
      setShowJump(true);
    }
  }, [messages.length, scrollToBottom]);

  // Reverse paging: keep the viewport anchored on the previously-oldest item.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !prevScrollHeightRef.current) return;
    const delta = el.scrollHeight - prevScrollHeightRef.current;
    if (delta > 0) {
      el.scrollTop += delta;
    }
    prevScrollHeightRef.current = 0;
  }, [messages.length]);

  /** Jump to the newest message and advance the read watermark. */
  const jumpToLatest = useCallback(() => {
    atBottomRef.current = true;
    setShowJump(false);
    setUnreadAnchorId(null);
    scrollToBottom("smooth");
    const last = useMessageStore
      .getState()
      .feeds[`${chatType}:${conversationId}`]?.messages.slice(-1)[0];
    if (last && last.senderUsername !== myUsername && last.id) {
      useSocketStore.getState().sendRead(conversationId, last.id);
    }
    // The WS read ack usually clears the counter; keep the UI honest when
    // the frame is throttled or the socket is briefly offline.
    useConversationStore.getState().markReadLocally(chatType, conversationId);
  }, [chatType, conversationId, myUsername, scrollToBottom]);

  /* ----------------------------- Read watermarks ---------------------------- */

  useEffect(() => {
    if (!lastMessageId) return;
    if (!atBottomRef.current) return;
    if (!useSocketStore.getState().isConversationVisible(conversationId)) return;
    if (!lastMessage || lastMessage.senderUsername === myUsername) return;
    useSocketStore.getState().sendRead(conversationId, lastMessageId);
  }, [lastMessageId, lastMessage, conversationId, myUsername]);

  // Window focus can reveal messages that arrived while unfocused.
  useEffect(() => {
    const onFocus = () => {
      if (!atBottomRef.current) return;
      if (!lastMessage) return;
      if (lastMessage.senderUsername === myUsername) return;
      useSocketStore.getState().sendRead(conversationId, lastMessage.id ?? 0);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [conversationId, lastMessage, myUsername]);

  /* --------------------------------- Render --------------------------------- */

  if (!feed || feed.loading) {
    return (
      <div className="flex-1 space-y-4 overflow-hidden p-6">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className={cn("flex", i % 2 ? "justify-end" : "justify-start")}>
            <Skeleton className="h-10 w-48 rounded-2xl" />
          </div>
        ))}
      </div>
    );
  }

  if (feed.error && messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-6">
        <p className="text-sm text-destructive">{feed.error}</p>
      </div>
    );
  }

  return (
    <div className="relative flex-1 min-h-0">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="chat-scroll absolute inset-0 overflow-y-auto py-4"
        aria-label="Message history"
      >
        {feed.hasOlder && (
          <div className="flex justify-center py-2">
            {feed.loadingOlder ? (
              <Skeleton className="h-6 w-28 rounded-full" />
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="rounded-full text-xs"
                onClick={() => {
                  prevScrollHeightRef.current = containerRef.current?.scrollHeight ?? 0;
                  void useMessageStore.getState().loadOlder(chatType, conversationId);
                }}
              >
                Load older messages
              </Button>
            )}
          </div>
        )}

        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-muted-foreground">
              No messages yet — say hello!
            </p>
          </div>
        ) : (
          messages.map((message, index) => {
            const previous = index > 0 ? messages[index - 1] : null;
            const next = index < messages.length - 1 ? messages[index + 1] : null;
            const showUnreadDivider =
              unreadAnchorId !== null && message.id === unreadAnchorId;
            const dayChanged =
              !previous ||
              formatDayDivider(previous.createdAt) !== formatDayDivider(message.createdAt);
            const dayChangedNext =
              next &&
              formatDayDivider(next.createdAt) !== formatDayDivider(message.createdAt);

            const mineSelf =
              message.senderUsername === myUsername || message.senderUsername === "__me__";
            const createdAt = new Date(message.createdAt).getTime();
            const groupedWithPrev = Boolean(
              previous &&
                !dayChanged &&
                previous.senderUsername === message.senderUsername &&
                createdAt - new Date(previous.createdAt).getTime() < GROUPING_WINDOW_MS
            );
            const groupedWithNext = Boolean(
              next &&
                !dayChangedNext &&
                next.senderUsername === message.senderUsername &&
                new Date(next.createdAt).getTime() - createdAt < GROUPING_WINDOW_MS
            );

            // Group consecutive messages from the same sender (within 5 min).
            const showSender =
              chatType === "group" &&
              !mineSelf &&
              !groupedWithPrev;

            return (
              <div
                key={message.clientMessageId || `m-${message.id}-${index}`}
                data-mid={message.id ?? undefined}
                className={cn(groupedWithNext ? "pb-0.5" : "pb-2")}
              >
                {dayChanged && (
                  <div className="mb-3 mt-1 flex items-center gap-3 px-4" aria-hidden>
                    <div className="h-px flex-1 bg-border" />
                    <span className="rounded-full border bg-background px-2.5 py-0.5 text-[11px] text-muted-foreground">
                      {formatDayDivider(message.createdAt)}
                    </span>
                    <div className="h-px flex-1 bg-border" />
                  </div>
                )}
                {showUnreadDivider && (
                  <div
                    className="mb-2.5 mt-1 flex items-center gap-3 px-4"
                    role="separator"
                    aria-label="Unread messages"
                    data-unread-divider
                  >
                    <div className="h-px flex-1 bg-sky-500/40" />
                    <span className="rounded-full border border-sky-500/40 bg-sky-500/10 px-2.5 py-0.5 text-[11px] font-medium text-sky-600 dark:text-sky-400">
                      Unread messages
                    </span>
                    <div className="h-px flex-1 bg-sky-500/40" />
                  </div>
                )}
                <MessageItem
                  message={message}
                  mine={mineSelf}
                  showSender={showSender}
                  variant={chatType}
                  groupedWithPrev={groupedWithPrev}
                  groupedWithNext={groupedWithNext}
                />
              </div>
            );
          })
        )}
        <div className="h-2" />
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0">
        {typingUsernames.length > 0 && <TypingIndicator usernames={typingUsernames} />}
        {showJump && (
          <div className="pointer-events-auto flex justify-end p-3">
            <Button
              size="icon"
              className="relative size-10 rounded-full shadow-lg"
              aria-label={
                unreadCount > 0
                  ? `Jump to latest messages (${unreadCount} unread)`
                  : "Jump to latest messages"
              }
              onClick={jumpToLatest}
            >
              <ArrowDown className="size-4" />
              {unreadCount > 0 && (
                <span
                  className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-destructive-foreground shadow"
                  aria-hidden
                >
                  {unreadCount > 99 ? "99+" : unreadCount}
                </span>
              )}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
