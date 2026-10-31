"use client";

/**
 * "Your email is not verified yet" warnings with a resend action.
 *
 * POST /auth/email/verify/request is throttled per account and per client
 * address, so the resend button enforces a client-side cooldown after every
 * send and additionally honors a backend `429` + `Retry-After` by disabling
 * itself until the server-side window passes.
 *
 * Two presentations share the same logic:
 * - `UnverifiedEmailBanner` — roomy card for the Settings profile tab.
 * - `UnverifiedEmailSidebarCard` — compact card pinned above the chat list
 *   in the sidebar header, so unverified accounts cannot miss it wherever
 *   they navigate inside the app.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, MailWarning, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiClient, ApiError } from "@/lib/api-client";
import type { MessageResponse } from "@/types/api";

/** Client-side resend cooldown (the backend throttles per account + IP). */
export const RESEND_VERIFICATION_COOLDOWN_S = 60;

/** Shared resend logic: client cooldown + backend 429 Retry-After handling. */
function useResendVerification() {
  const [sending, setSending] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  // One tick per second until the cooldown drains to zero.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  async function resend() {
    setSending(true);
    try {
      const result = await apiClient.post<MessageResponse>(
        "/api/v1/auth/email/verify/request"
      );
      toast.success(result.message || "Verification email sent.");
      setCooldown(RESEND_VERIFICATION_COOLDOWN_S);
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        const wait = error.retryAfterSeconds ?? RESEND_VERIFICATION_COOLDOWN_S;
        setCooldown(wait);
        toast.error(
          `Too many requests. You can resend in ${wait}s.`
        );
      } else {
        toast.error(
          error instanceof ApiError
            ? error.message
            : "Could not send the verification email."
        );
      }
    } finally {
      setSending(false);
    }
  }

  return { sending, cooldown, resend };
}

/** Roomy card used on the Settings profile tab. */
export function UnverifiedEmailBanner({ email }: { email: string }) {
  const { sending, cooldown, resend } = useResendVerification();
  const cooling = cooldown > 0;

  return (
    <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
      <p className="flex items-center gap-2 text-sm font-medium text-amber-600 dark:text-amber-500">
        <MailWarning className="size-4" /> Your email is not verified yet.
      </p>
      <p className="text-xs text-muted-foreground">
        Open the link we sent to {email}, or paste the token on the verify
        page. Did not arrive? Send it again.
      </p>
      <Button
        variant="outline"
        size="sm"
        onClick={() => void resend()}
        disabled={sending || cooling}
      >
        {sending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Send className="size-4" />
        )}
        {sending
          ? "Sending…"
          : cooling
            ? `Resend available in ${cooldown}s`
            : "Resend verification email"}
      </Button>
    </div>
  );
}

/**
 * Compact card pinned above the chat list (ChatSidebar header). Same amber
 * treatment as the Settings card, sized for the 320–384 px column, with a
 * full-width resend button that shows the same cooldown states.
 */
export function UnverifiedEmailSidebarCard({ email }: { email: string }) {
  const { sending, cooldown, resend } = useResendVerification();
  const cooling = cooldown > 0;

  return (
    <div
      role="alert"
      aria-label="Email verification pending"
      className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-2.5"
    >
      <div className="flex items-start gap-2.5">
        <MailWarning className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-500" />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium leading-4 text-amber-600 dark:text-amber-500">
            Your email is not verified yet.
          </p>
          <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
            We sent a link to{" "}
            <span dir="auto" className="font-medium text-foreground">
              {email}
            </span>
            . Did not arrive? Send it again.
          </p>
        </div>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="mt-2 h-7 w-full gap-1.5 text-[11px]"
        onClick={() => void resend()}
        disabled={sending || cooling}
      >
        {sending ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Send className="size-3.5" />
        )}
        {sending
          ? "Sending…"
          : cooling
            ? `Resend available in ${cooldown}s`
            : "Resend verification email"}
      </Button>
    </div>
  );
}
