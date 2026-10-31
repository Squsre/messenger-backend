"use client";

import { cn } from "@/lib/utils";

export function TypingIndicator({
  usernames,
  className,
}: {
  usernames: string[];
  className?: string;
}) {
  if (usernames.length === 0) return null;
  const label =
    usernames.length === 1
      ? `${usernames[0]} is typing`
      : usernames.length === 2
        ? `${usernames[0]} and ${usernames[1]} are typing`
        : "Several people are typing";

  return (
    <div
      className={cn("flex items-center gap-2 px-4 pb-1 text-xs text-muted-foreground", className)}
      role="status"
      aria-live="polite"
    >
      <span className="flex items-end gap-0.5" aria-hidden>
        <span className="typing-dot size-1.5 rounded-full bg-muted-foreground" />
        <span className="typing-dot size-1.5 rounded-full bg-muted-foreground" />
        <span className="typing-dot size-1.5 rounded-full bg-muted-foreground" />
      </span>
      <span>{label}…</span>
    </div>
  );
}
