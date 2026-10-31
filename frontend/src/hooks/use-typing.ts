"use client";

/**
 * useTypingUsers — typing state for one feed, without breaking React's
 * useSyncExternalStore snapshot contract.
 *
 * Why this hook exists:
 *   Zustand v5 + React 19 requires a selector's result to be referentially
 *   stable while the underlying state is unchanged. Selectors like
 *   `s => Object.keys(map).filter(...)` return a BRAND-NEW array on every
 *   call, so getSnapshot never matches, and the component re-renders in an
 *   infinite loop ("The result of getSnapshot should be cached…" followed by
 *   "Maximum update depth exceeded").
 *
 * The fix: select the `typing` map by REFERENCE (stable under immer's
 * structural sharing — it only changes when typing state actually changes)
 * and derive the username list in useMemo.
 *
 * While someone is typing, a 1 s ticker prunes expired entries so the
 * indicator fades even when the peer never sent an explicit stop frame.
 */
import { useEffect, useMemo } from "react";
import { useMessageStore } from "@/stores/use-message-store";
import type { ChatType } from "@/stores/use-conversation-store";

export function useTypingUsers(
  chatType: ChatType,
  conversationId: number,
  options?: { excludeUsername?: string }
): string[] {
  const exclude = options?.excludeUsername;

  // Reference-stable selection: `feed.typing` keeps its identity until a
  // typing frame mutates it (immer structural sharing).
  const typingMap = useMessageStore(
    (s) => s.feeds[`${chatType}:${conversationId}`]?.typing
  );

  const usernames = useMemo(() => {
    if (!typingMap) return [] as string[];
    const now = Date.now();
    return Object.keys(typingMap)
      .filter((u) => typingMap[u] > now && u !== exclude)
      .sort();
  }, [typingMap, exclude]);

  const hasTyping = usernames.length > 0;
  useEffect(() => {
    if (!hasTyping) return;
    const timer = setInterval(() => {
      useMessageStore.getState().pruneTyping(conversationId);
    }, 1000);
    return () => clearInterval(timer);
  }, [hasTyping, conversationId]);

  return usernames;
}
