"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function FullScreenLoader({ label, className }: { label?: string; className?: string }) {
  return (
    <div
      className={cn(
        "flex h-dvh w-full flex-col items-center justify-center gap-3 bg-background",
        className
      )}
      role="status"
      aria-live="polite"
    >
      <Loader2 className="size-6 animate-spin text-muted-foreground" />
      {label && <p className="text-sm text-muted-foreground">{label}</p>}
    </div>
  );
}
