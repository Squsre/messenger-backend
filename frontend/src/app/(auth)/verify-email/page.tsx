"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { BadgeCheck, Loader2, MailCheck } from "lucide-react";
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
import { apiClient, ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/use-auth-store";
import { verifyEmailSchema, type VerifyEmailValues } from "@/lib/validation";

function VerifyEmailInner() {
  const params = useSearchParams();
  const [done, setDone] = useState(false);
  // The token can be consumed while signed in (banner deep-link) or from a
  // logged-out inbox link; the success card adapts to both cases.
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const form = useForm<VerifyEmailValues>({
    resolver: zodResolver(verifyEmailSchema),
    defaultValues: { token: "" },
  });

  const confirmToken = useCallback(
    async (rawToken: string) => {
      try {
        await apiClient.post("/api/v1/auth/email/verify/confirm", {
          token: rawToken.trim(),
        });
        setDone(true);
        toast.success("Email verified.");
        if (useAuthStore.getState().isAuthenticated) {
          // Refresh the private profile so email_verified flips and the
          // unverified banner disappears on the next screen.
          void useAuthStore.getState().refreshProfile();
        }
      } catch {
        toast.error("This link is invalid or has expired.");
      }
    },
    []
  );

  const linkToken = params.get("token") ?? "";
  useEffect(() => {
    if (!linkToken) return;
    form.setValue("token", linkToken);
    // Defer out of the effect body; the request itself is async anyway.
    const timer = setTimeout(() => void confirmToken(linkToken), 0);
    return () => clearTimeout(timer);
  }, [linkToken, form, confirmToken]);

  async function onSubmit(values: VerifyEmailValues) {
    await confirmToken(values.token);
  }

  if (done) {
    return (
      <Card>
        <CardHeader className="items-center text-center">
          <MailCheck className="size-10 text-emerald-500 mx-auto" />
          <CardTitle className="font-display text-xl mt-2">Email verified</CardTitle>
          <CardDescription>Your address is confirmed.</CardDescription>
        </CardHeader>
        <CardFooter>
          <Button asChild className="w-full">
            <Link href={isAuthenticated ? "/" : "/login"}>
              {isAuthenticated ? "Go to your chats" : "Go to sign in"}
            </Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-display text-xl">Verify your email</CardTitle>
        <CardDescription>
          Open the verification link from your inbox, or paste the token below.
        </CardDescription>
      </CardHeader>
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="token">Verification token</Label>
            <Input
              id="token"
              placeholder="Paste the token from the link"
              {...form.register("token")}
            />
            {form.formState.errors.token && (
              <p className="text-sm text-destructive">
                {form.formState.errors.token.message}
              </p>
            )}
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-3 mt-6">
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            {form.formState.isSubmitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <BadgeCheck className="size-4" />
            )}
            Confirm email
          </Button>
          <p className="text-sm text-muted-foreground">
            <Link href="/login" className="text-primary hover:underline">
              Back to sign in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={null}>
      <VerifyEmailInner />
    </Suspense>
  );
}
