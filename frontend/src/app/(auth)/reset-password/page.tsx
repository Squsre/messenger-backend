"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { KeyRound, Loader2, MailCheck, Send } from "lucide-react";
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
import {
  resetConfirmSchema,
  resetRequestSchema,
  type ResetConfirmValues,
  type ResetRequestValues,
} from "@/lib/validation";
import { useUiStore } from "@/stores/use-ui-store";
import { PasswordPolicyHints } from "@/components/auth/password-policy-hints";

type Step = "request" | "confirm" | "done";

function ResetPasswordInner() {
  const params = useSearchParams();
  const emailHint = useUiStore((s) => s.authEmailHint);
  // A ?token= link jumps straight to the confirm step.
  const linkToken = params.get("token") ?? "";
  const [step, setStep] = useState<Step>(() => (linkToken ? "confirm" : "request"));

  const requestForm = useForm<ResetRequestValues>({
    resolver: zodResolver(resetRequestSchema),
    defaultValues: { email: emailHint ?? "" },
  });

  const confirmForm = useForm<ResetConfirmValues>({
    resolver: zodResolver(resetConfirmSchema),
    defaultValues: { token: linkToken, newPassword: "", confirmPassword: "" },
  });
  // React-Compiler-friendly subscription (form.watch() trips the linter).
  const newPassword = useWatch({ control: confirmForm.control, name: "newPassword" }) ?? "";

  async function onRequest(values: ResetRequestValues) {
    try {
      // The answer is identical for known and unknown addresses by design.
      await apiClient.post("/api/v1/auth/password/reset/request", values);
      toast.success(
        "If the address belongs to an account, a reset link is on its way."
      );
      setStep("confirm");
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not send the request."
      );
    }
  }

  async function onConfirm(values: ResetConfirmValues) {
    try {
      await apiClient.post("/api/v1/auth/password/reset/confirm", {
        token: values.token.trim(),
        new_password: values.newPassword,
      });
      setStep("done");
      toast.success("Password updated. Sign in with the new password.");
    } catch (error) {
      toast.error(
        error instanceof ApiError
          ? "This reset link is invalid or has expired."
          : "Could not reset the password."
      );
    }
  }

  if (step === "done") {
    return (
      <Card>
        <CardHeader className="items-center text-center">
          <MailCheck className="size-10 text-emerald-500 mx-auto" />
          <CardTitle className="font-display text-xl mt-2">Password updated</CardTitle>
          <CardDescription>
            Every previous session was signed out.
          </CardDescription>
        </CardHeader>
        <CardFooter>
          <Button asChild className="w-full">
            <Link href="/login">Go to sign in</Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }

  if (step === "request") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="font-display text-xl">Reset your password</CardTitle>
          <CardDescription>
            Enter your email and we will send a reset link.
          </CardDescription>
        </CardHeader>
        <form onSubmit={requestForm.handleSubmit(onRequest)} noValidate>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                {...requestForm.register("email")}
              />
              {requestForm.formState.errors.email && (
                <p className="text-sm text-destructive">
                  {requestForm.formState.errors.email.message}
                </p>
              )}
            </div>
          </CardContent>
          <CardFooter className="flex flex-col gap-3 mt-6">
            <Button
              type="submit"
              className="w-full"
              disabled={requestForm.formState.isSubmitting}
            >
              {requestForm.formState.isSubmitting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              Send reset link
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

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-display text-xl">Choose a new password</CardTitle>
        <CardDescription>
          Paste the token from the reset link and pick a strong password.
        </CardDescription>
      </CardHeader>
      <form onSubmit={confirmForm.handleSubmit(onConfirm)} noValidate>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="token">Reset token</Label>
            <Input
              id="token"
              placeholder="From the reset link"
              {...confirmForm.register("token")}
            />
            {confirmForm.formState.errors.token && (
              <p className="text-sm text-destructive">
                {confirmForm.formState.errors.token.message}
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="newPassword">New password</Label>
            <Input
              id="newPassword"
              type="password"
              autoComplete="new-password"
              {...confirmForm.register("newPassword")}
            />
            <PasswordPolicyHints password={newPassword} />
            {confirmForm.formState.errors.newPassword && (
              <p className="text-sm text-destructive">
                {confirmForm.formState.errors.newPassword.message}
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirmPassword">Confirm new password</Label>
            <Input
              id="confirmPassword"
              type="password"
              autoComplete="new-password"
              {...confirmForm.register("confirmPassword")}
            />
            {confirmForm.formState.errors.confirmPassword && (
              <p className="text-sm text-destructive">
                {confirmForm.formState.errors.confirmPassword.message}
              </p>
            )}
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-3 mt-6">
          <Button
            type="submit"
            className="w-full"
            disabled={confirmForm.formState.isSubmitting}
          >
            {confirmForm.formState.isSubmitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <KeyRound className="size-4" />
            )}
            Update password
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordInner />
    </Suspense>
  );
}
