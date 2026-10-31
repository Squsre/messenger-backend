"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { toast } from "sonner";
import {
  BookUser,
  Compass,
  MessagesSquare,
  Moon,
  Settings,
  Sun,
  UserPlus,
  UsersRound,
  LogOut,
  Bell,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useUiStore } from "@/stores/use-ui-store";
import { useAuthStore } from "@/stores/use-auth-store";
import { useConversationStore } from "@/stores/use-conversation-store";
import { cn, displayNameOf, initialsOf } from "@/lib/utils";

function RailButton({
  label,
  active,
  badge,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  badge?: number;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={label}
          onClick={onClick}
          className={cn(
            "relative size-10 rounded-xl text-muted-foreground hover:text-foreground",
            active && "bg-primary/10 text-primary hover:text-primary"
          )}
        >
          {children}
          {badge !== undefined && badge > 0 && (
            <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-destructive-foreground">
              {badge > 9 ? "9+" : badge}
            </span>
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

export function NavRail() {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const pendingInvitations = useConversationStore((s) => s.pendingInvitations.length);
  const setNewChatOpen = useUiStore((s) => s.setNewChatOpen);
  const setCreateGroupOpen = useUiStore((s) => s.setCreateGroupOpen);
  const setInvitationsOpen = useUiStore((s) => s.setInvitationsOpen);
  const setDiscoverGroupsOpen = useUiStore((s) => s.setDiscoverGroupsOpen);

  const inSettings = pathname.startsWith("/settings");
  const inContacts = pathname.startsWith("/contacts");

  return (
    <nav
      aria-label="Primary"
      className="hidden sm:flex w-16 shrink-0 flex-col items-center gap-2 border-r bg-background py-4"
    >
      <Link
        href="/"
        aria-label="Messenger home"
        className="mb-2 flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground"
      >
        <MessagesSquare className="size-5" />
      </Link>

      <RailButton label="New chat" onClick={() => setNewChatOpen(true)}>
        <UserPlus className="size-5" />
      </RailButton>
      <RailButton label="New group" onClick={() => setCreateGroupOpen(true)}>
        <UsersRound className="size-5" />
      </RailButton>
      <RailButton
        label="Discover public groups"
        onClick={() => setDiscoverGroupsOpen(true)}
      >
        <Compass className="size-5" />
      </RailButton>
      <RailButton
        label="Contacts"
        active={inContacts}
        onClick={() => router.push("/contacts")}
      >
        <BookUser className="size-5" />
      </RailButton>
      <RailButton
        label="Invitations"
        badge={pendingInvitations}
        onClick={() => setInvitationsOpen(true)}
      >
        <Bell className="size-5" />
      </RailButton>

      <div className="mt-auto flex flex-col items-center gap-2">
        <RailButton
          label={theme === "dark" ? "Light mode" : "Dark mode"}
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
        </RailButton>
        <RailButton label="Settings" active={inSettings} onClick={() => router.push("/settings")}>
          <Settings className="size-5" />
        </RailButton>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              aria-label="Account menu"
              className="mt-1 rounded-full outline-none ring-primary focus-visible:ring-2"
            >
              <Avatar className="size-9">
                <AvatarFallback className="text-xs font-semibold bg-primary text-primary-foreground">
                  {initialsOf(displayNameOf(user?.display_name, user?.username ?? ""), user?.username ?? "?")}
                </AvatarFallback>
              </Avatar>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right" align="end" className="w-52">
            <DropdownMenuLabel>
              <div className="font-medium">{displayNameOf(user?.display_name, user?.username ?? "")}</div>
              <div className="text-xs font-normal text-muted-foreground">@{user?.username}</div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => router.push("/settings")}>
              <Settings className="size-4" /> Settings
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              onClick={() => {
                void logout();
              }}
            >
              <LogOut className="size-4" /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
