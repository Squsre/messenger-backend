"use client";

/**
 * Shared password-policy checklist (12–128 chars + the four character
 * classes). Used by the register screen, the settings password tab and the
 * password-reset flow so every entry point teaches the exact same
 * Argon2id-compatible policy the backend enforces.
 */
import { useMemo } from "react";
import { Check, X } from "lucide-react";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  PASSWORD_POLICY_REGEXES,
} from "@/lib/constants";
import { cn } from "@/lib/utils";

export interface PasswordChecks {
  length: boolean;
  rules: { hint: string; ok: boolean }[];
}

export function usePasswordChecks(password: string): PasswordChecks {
  return useMemo(
    () => ({
      length:
        password.length >= PASSWORD_MIN_LENGTH &&
        password.length <= PASSWORD_MAX_LENGTH,
      rules: PASSWORD_POLICY_REGEXES.map(({ regex, hint }) => ({
        hint,
        ok: regex.test(password),
      })),
    }),
    [password]
  );
}

export function PasswordPolicyHints({
  password,
  className,
}: {
  password: string;
  className?: string;
}) {
  const checks = usePasswordChecks(password);
  return (
    <ul
      className={cn(
        "grid grid-cols-2 gap-1 text-xs text-muted-foreground",
        className
      )}
      aria-label="Password policy checklist"
    >
      <li
        className={cn(
          "flex items-center gap-1",
          checks.length && "text-emerald-600 dark:text-emerald-400"
        )}
      >
        {checks.length ? <Check className="size-3" /> : <X className="size-3" />}
        {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters
      </li>
      {checks.rules.map((rule) => (
        <li
          key={rule.hint}
          className={cn(
            "flex items-center gap-1",
            rule.ok && "text-emerald-600 dark:text-emerald-400"
          )}
        >
          {rule.ok ? <Check className="size-3" /> : <X className="size-3" />}
          {rule.hint}
        </li>
      ))}
    </ul>
  );
}
