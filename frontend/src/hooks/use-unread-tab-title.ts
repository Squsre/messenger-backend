"use client";

/**
 * Unread tab-title badge: while any conversation (direct or group) has
 * unread messages, the browser tab shows a "(N)" prefix — `(3) Messenger —
 * Private realtime chat` — so unread traffic is visible without even
 * glancing at the app. Clears back to the plain title at zero.
 */
import { useEffect } from "react";
import { useConversationStore } from "@/stores/use-conversation-store";
import { APP_TITLE } from "@/lib/constants";

export function useUnreadTabTitle(): void {
  // Primitive result — snapshot-stable (no fresh objects/arrays per call).
  const totalUnread = useConversationStore((s) => {
    let sum = 0;
    for (const c of s.conversations) sum += c.unread_count;
    for (const g of s.groups) sum += g.unread_count;
    return sum;
  });

  useEffect(() => {
    document.title =
      totalUnread > 0 ? `(${totalUnread > 99 ? "99+" : totalUnread}) ${APP_TITLE}` : APP_TITLE;
    return () => {
      document.title = APP_TITLE;
    };
  }, [totalUnread]);
}
