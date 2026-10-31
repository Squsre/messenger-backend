"use client";

import { AlertCircle, Check, CheckCheck, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MessageStatus } from "@/stores/use-message-store";

interface StatusTickProps {
  status: MessageStatus;
  /** Direct chats show timestamps ticks; groups show a read count instead. */
  variant: "direct" | "group";
  /** For groups: readCount / (memberCount - 1). */
  readCount?: number;
  otherMembers?: number;
}

/**
 * Message status indicators:
 *   pending  -> clock
 *   failed   -> alert icon
 *   sent     -> single check
 *   delivered-> double check (gray)
 *   read     -> double check (accent)
 * Group messages show "n/m" read badge for my own messages instead.
 */
export function StatusTick({ status, variant, readCount = 0, otherMembers = 0 }: StatusTickProps) {
  if (variant === "group") {
    if (otherMembers <= 0) return null;
    return (
      <span className="inline-flex items-center text-[11px] leading-none text-muted-foreground tabular-nums">
        {readCount}/{otherMembers}
        <CheckCheck className="ml-0.5 size-3.5" aria-label={`${readCount} of ${otherMembers} read`} />
      </span>
    );
  }

  switch (status) {
    case "pending":
      return <Clock className="size-3.5 text-muted-foreground/70" aria-label="Sending" />;
    case "failed":
      return <AlertCircle className="size-3.5 text-destructive" aria-label="Failed" />;
    case "sent":
      return <Check className="size-3.5 text-muted-foreground" aria-label="Sent" />;
    case "delivered":
      return (
        <CheckCheck className="size-3.5 text-muted-foreground" aria-label="Delivered" />
      );
    case "read":
      return (
        <CheckCheck className="size-3.5 text-sky-500 dark:text-sky-400" aria-label="Read" />
      );
    default:
      return null;
  }
}
