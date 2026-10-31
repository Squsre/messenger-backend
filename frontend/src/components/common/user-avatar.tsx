"use client";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { cn, avatarTone, displayNameOf, initialsOf } from "@/lib/utils";

interface UserAvatarProps {
  name: string | null | undefined;
  username: string;
  className?: string;
  /** Small "recently active" indicator. */
  showPresence?: boolean;
  presenceOnline?: boolean;
}

export function UserAvatar({
  name,
  username,
  className,
  showPresence = false,
  presenceOnline = false,
}: UserAvatarProps) {
  return (
    <span className="relative inline-flex shrink-0">
      <Avatar className={cn("size-10", className)}>
        <AvatarFallback
          className={cn("text-xs font-semibold select-none", avatarTone(username))}
        >
          {initialsOf(displayNameOf(name, username), username)}
        </AvatarFallback>
      </Avatar>
      {showPresence && (
        <span
          aria-hidden
          className={cn(
            "absolute -bottom-0.5 -right-0.5 size-3 rounded-full border-2 border-background",
            presenceOnline ? "bg-emerald-500" : "bg-zinc-400 dark:bg-zinc-600"
          )}
        />
      )}
    </span>
  );
}

export function GroupAvatar({
  name,
  className,
  memberCount,
}: {
  name: string;
  className?: string;
  memberCount?: number;
}) {
  return (
    <span className="relative inline-flex shrink-0">
      <span
        className={cn(
          "flex size-10 items-center justify-center rounded-lg bg-primary/15 text-primary",
          className
        )}
      >
        {memberCount !== undefined && memberCount > 2 ? (
          <span className="flex -space-x-1.5">
            <span className="size-4 rounded-full bg-primary/70 border border-background" />
            <span className="size-4 rounded-full bg-primary/45 border border-background" />
          </span>
        ) : (
          <span className="text-sm font-bold">{initialsOf(name, name)}</span>
        )}
      </span>
    </span>
  );
}
