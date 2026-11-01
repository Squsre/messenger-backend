"use client";

/**
 * Mobile bottom navigation — the phone replacement for the desktop nav rail
 * (which is hidden below `sm`). Five entries only: the two create actions,
 * the group-invitation inbox (with its pending badge) and the two full-page
 * destinations. Discover-groups, the command palette and contacts quick
 * access stay in the chat-list header; the theme switch and sign-out live
 * in Settings → Appearance / Devices.
 *
 * Rendered in normal flow at the bottom of the app shell column (not
 * `fixed`), so no page needs bottom padding compensation and the chat
 * composer always sits directly above it.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, BookUser, Settings, UserPlus, UsersRound } from "lucide-react";
import { useUiStore } from "@/stores/use-ui-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { cn } from "@/lib/utils";

function BottomNavItem({
  label,
  active,
  badge,
  onClick,
  href,
  children,
}: {
  label: string;
  active?: boolean;
  badge?: number;
  /** When set the item renders as a link, otherwise as a button. */
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  const className = cn(
    "relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[10px] font-medium leading-none transition-colors",
    active ? "text-primary" : "text-muted-foreground hover:text-foreground"
  );
  const inner = (
    <>
      <span className="relative">
        {children}
        {badge !== undefined && badge > 0 && (
          <span className="absolute -right-2 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[9px] font-semibold text-destructive-foreground">
            {badge > 9 ? "9+" : badge}
          </span>
        )}
      </span>
      <span className="w-full truncate text-center">{label}</span>
    </>
  );

  if (href) {
    return (
      <Link
        href={href}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        className={className}
      >
        {inner}
      </Link>
    );
  }
  return (
    <button type="button" aria-label={label} onClick={onClick} className={className}>
      {inner}
    </button>
  );
}

export function BottomNav() {
  const pathname = usePathname();
  const pendingInvitations = useConversationStore((s) => s.pendingInvitations.length);

  return (
    <nav
      aria-label="Primary"
      className="grid h-16 shrink-0 grid-cols-5 items-stretch border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden"
    >
      <BottomNavItem
        label="New chat"
        onClick={() => useUiStore.getState().setNewChatOpen(true)}
      >
        <UserPlus className="size-5" />
      </BottomNavItem>
      <BottomNavItem
        label="New group"
        onClick={() => useUiStore.getState().setCreateGroupOpen(true)}
      >
        <UsersRound className="size-5" />
      </BottomNavItem>
      <BottomNavItem
        label="Invites"
        badge={pendingInvitations}
        onClick={() => useUiStore.getState().setInvitationsOpen(true)}
      >
        <Bell className="size-5" />
      </BottomNavItem>
      <BottomNavItem label="Contacts" href="/contacts" active={pathname.startsWith("/contacts")}>
        <BookUser className="size-5" />
      </BottomNavItem>
      <BottomNavItem label="Settings" href="/settings" active={pathname.startsWith("/settings")}>
        <Settings className="size-5" />
      </BottomNavItem>
    </nav>
  );
}
