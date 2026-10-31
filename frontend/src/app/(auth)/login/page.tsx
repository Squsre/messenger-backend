"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Loader2, LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/stores/use-auth-store";
import { useUiStore } from "@/stores/use-ui-store";
import { loginSchema, type LoginValues } from "@/lib/validation";
import { ApiError } from "@/lib/api-client";

const IS_SANDBOX = !process.env.NEXT_PUBLIC_API_BASE_URL;
const DEMO_PASSWORD = "Str0ng-Passw0rd!";

/** Fallback when the backend sends 429 without a usable Retry-After. */
const DEFAULT_RETRY_AFTER_S = 60;

export default function LoginPage() {
  const router = useRouter();
  const login = useAuthStore((s) => s.login);
  const setAuthEmailHint = useUiStore((s) => s.setAuthEmailHint);
  const [demoBusy, setDemoBusy] = useState<string | null>(null);
  /** Seconds left before the form re-enables after a 429 rate limit. */
  const [retryIn, setRetryIn] = useState(0);

  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  // Polite notice after forceLogout sent us to /login?expired=1 (token
  // expiry, revocation in another tab, or logout-all on another device).
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.location.search.includes("expired=1")) {
      toast.info("Your session has ended. Please sign in again.");
    }
  }, []);

  // One tick per second until the 429 cooldown drains away.
  useEffect(() => {
    if (retryIn <= 0) return;
    const timer = setTimeout(() => setRetryIn((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [retryIn]);

  function applyLoginError(error: unknown) {
    if (error instanceof ApiError) {
      if (error.status === 403) {
        form.setError("root", {
          message:
            "This account has been disabled. Contact support if you believe this is a mistake.",
        });
      } else if (error.status === 429) {
        const wait = error.retryAfterSeconds ?? DEFAULT_RETRY_AFTER_S;
        setRetryIn(wait);
        form.setError("root", {
          message: `Too many sign-in attempts. Try again in ${wait}s.`,
        });
      } else if (error.status === 401) {
        // The backend deliberately does not reveal whether the email exists.
        form.setError("root", { message: "Incorrect email or password." });
      } else {
        form.setError("root", { message: error.message });
      }
    } else {
      form.setError("root", {
        message: "Could not sign in. Please try again.",
      });
    }
  }

  async function onSubmit(values: LoginValues) {
    try {
      await login(values);
      toast.success("Welcome back!");
      window.location.href = "/"; // <-- به جای router.replace("/")
    } catch (error) {
      applyLoginError(error);
    }
  }

  async function loginAs(email: string) {
    if (retryIn > 0) return;
    setDemoBusy(email);
    try {
      await login({ email, password: DEMO_PASSWORD });
      toast.success(`Signed in as ${email.split("@")[0]}`);
      window.location.href = "/"; // <-- به جای router.replace("/")
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        setRetryIn(error.retryAfterSeconds ?? DEFAULT_RETRY_AFTER_S);
        toast.error(
          `Too many attempts. Try again in ${error.retryAfterSeconds ?? DEFAULT_RETRY_AFTER_S}s.`
        );
      } else {
        toast.error(
          error instanceof ApiError ? error.message : "Demo login failed."
        );
      }
    } finally {
      setDemoBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-display text-xl">Sign in</CardTitle>
        <CardDescription>
          Use your email and password to reach your chats.
        </CardDescription>
      </CardHeader>
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              {...form.register("email")}
            />
            {form.formState.errors.email && (
              <p className="text-sm text-destructive">
                {form.formState.errors.email.message}
              </p>
            )}
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="password">Password</Label>
              <Link
                href="/reset-password"
                className="text-xs text-muted-foreground hover:text-foreground"
                onClick={() => setAuthEmailHint(form.getValues("email"))}
              >
                Forgot password?
              </Link>
            </div>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              placeholder="••••••••••••"
              {...form.register("password")}
            />
            {form.formState.errors.password && (
              <p className="text-sm text-destructive">
                {form.formState.errors.password.message}
              </p>
            )}
          </div>
          {form.formState.errors.root && (
            <p className="text-sm text-destructive" role="alert">
              {form.formState.errors.root.message}
            </p>
          )}
        </CardContent>
        <CardFooter className="flex flex-col gap-3 mt-6">
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting || retryIn > 0}
          >
            {form.formState.isSubmitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <LogIn className="size-4" />
            )}
            {retryIn > 0 ? `Wait ${retryIn}s` : "Sign in"}
          </Button>
          {retryIn > 0 && (
            <p className="text-xs text-muted-foreground" role="status">
              Rate limit reached — retrying is possible in {retryIn}s.
            </p>
          )}
          <p className="text-sm text-muted-foreground">
            No account yet?{" "}
            <Link href="/register" className="text-primary hover:underline">
              Create one
            </Link>
          </p>
        </CardFooter>
      </form>

      {IS_SANDBOX && (
        <CardFooter className="flex-col items-stretch gap-2 border-t pt-4">
          <p className="text-xs text-muted-foreground text-center">
            Demo accounts (sandbox preview, seeded by the mock API)
          </p>
          <div className="grid grid-cols-3 gap-2">
            {["alice", "bob", "carol"].map((name) => (
              <Button
                key={name}
                type="button"
                variant="outline"
                size="sm"
                disabled={demoBusy !== null || retryIn > 0}
                onClick={() => loginAs(`${name}@example.com`)}
              >
                {demoBusy === `${name}@example.com` ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : null}
                {name}
              </Button>
            ))}
          </div>
        </CardFooter>
      )}
    </Card>
  );
}
