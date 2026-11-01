"use client";

/**
 * Application shell: auth guard, WebSocket lifecycle, and the responsive
 * multi-column frame (nav rail + chat sidebar + viewport + info drawer).
 *
 * The chat-list column can be minimized (a slim rail with an expand button
 * remains). Notification clicks navigate via the `messenger:navigate`
 * event dispatched from lib/notifications.
 */
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { MessagesSquare, PanelLeftOpen } from "lucide-react";
import { useAuthStore } from "@/stores/use-auth-store";
import { useSocketStore } from "@/stores/use-socket-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { useUiStore } from "@/stores/use-ui-store";
import { requestNotificationPermission } from "@/lib/notifications";
import { NavRail } from "@/components/layout/nav-rail";
import { ChatSidebar } from "@/components/layout/chat-sidebar";
import { InfoSidebar } from "@/components/layout/info-sidebar";
import { BottomNav } from "@/components/layout/bottom-nav";
import { DialogRoot } from "@/components/dialogs/dialog-root";
import { FullScreenLoader } from "@/components/common/full-screen-loader";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";
import { useUnreadTabTitle } from "@/hooks/use-unread-tab-title";
import { cn } from "@/lib/utils";

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const isMobile = useIsMobile();
  // "(N) Messenger…" tab title while unread messages exist.
  useUnreadTabTitle();

  const hydrated = useAuthStore((s) => s.hydrated);
  const token = useAuthStore((s) => s.token);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const leftCollapsed = useUiStore((s) => s.leftSidebarCollapsed);

  // Route protection complementing the middleware (cookie can be stale).
  useEffect(() => {
    if (hydrated && !token) {
      router.replace("/login");
    }
  }, [hydrated, token, router]);

  // Realtime + initial data load. The socket intentionally survives route
  // changes; it only dies on logout/reset.
  useEffect(() => {
    if (!token) return;
    useSocketStore.getState().connect(token);
    // Token liveness probe (GET /auth/me): detects sessions revoked in
    // another tab / by logout-all even before the socket reports 4401.
    void useAuthStore.getState().probeSession();
    void useConversationStore.getState().loadConversations(true);
    void useConversationStore.getState().loadGroups(true);
    void useConversationStore.getState().loadPendingInvitations(true);
  }, [token]);

  // Ask for notification permission once per session after sign-in.
  useEffect(() => {
    if (!token) return;
    void requestNotificationPermission();
  }, [token]);

  // Notification clicks: focus + navigate to the chat.
  useEffect(() => {
    const onNavigate = (event: Event) => {
      const target = (event as CustomEvent<string>).detail;
      if (typeof target === "string" && target.startsWith("/")) {
        router.push(target);
      }
    };
    window.addEventListener("messenger:navigate", onNavigate);
    return () => window.removeEventListener("messenger:navigate", onNavigate);
  }, [router]);

  if (!hydrated || !token || !isAuthenticated) {
    return <FullScreenLoader label="Loading your session…" />;
  }

  // On phones the chat list hides as soon as a chat or a full page (settings,
  // contacts) opens; the page then owns the whole width. At "/" the inverse
  // holds: the list owns the screen and the empty-state pane is hidden, so a
  // crushed sliver of it can never appear next to the list (single-view rule).
  const hideSidebarOnMobile =
    isMobile &&
    (pathname.startsWith("/c/") ||
      pathname.startsWith("/g/") ||
      pathname.startsWith("/settings") ||
      pathname.startsWith("/contacts"));
  const hideMainOnMobile = isMobile && pathname === "/";

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background">
      {/**
       * The multi-column frame lives in its own row; the mobile bottom nav
       * sits BELOW it in normal flow, so every page (list, chat, settings)
       * keeps its full height minus the bar and nothing needs fixed-offset
       * padding compensation.
       */}
      <div className="flex min-h-0 flex-1">
        <NavRail />
        {leftCollapsed ? (
          <div
            className={cn(
              "flex h-full w-12 shrink-0 flex-col items-center gap-3 border-r bg-background py-4",
              hideSidebarOnMobile && "hidden"
            )}
          >
            <Button
              variant="ghost"
              size="icon"
              aria-label="Expand chat list"
              title="Expand chat list"
              className="size-10 rounded-xl text-muted-foreground hover:text-foreground"
              onClick={() => useUiStore.getState().setLeftSidebarCollapsed(false)}
            >
              <PanelLeftOpen className="size-5" />
            </Button>
            <div className="mt-2 flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <MessagesSquare className="size-4" />
            </div>
          </div>
        ) : (
          /*
           * The width lives HERE, on the flex child, not on the inner aside:
           * an unwidth'd wrapper lets the flex algorithm shrink the chat list
           * below its target whenever the viewport narrows (the sidebar was
           * squeezed to ~300px at 640px and left a 76px main sliver on
           * phones). `shrink-0` + explicit widths keep the column stable
           * through any resize; below md the list is the full-width view.
           */
          <div
            className={cn(
              "h-full min-h-0 w-full shrink-0 md:w-80 lg:w-96",
              hideSidebarOnMobile && "hidden"
            )}
          >
            <ChatSidebar />
          </div>
        )}
        <main
          className={cn(
            "flex min-w-0 flex-1 flex-col",
            hideMainOnMobile && "hidden md:flex"
          )}
        >
          {children}
        </main>
        <InfoSidebar />
      </div>
      <BottomNav />
      <DialogRoot />
    </div>
  );
}
