"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Loader2, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/stores/use-auth-store";
import { registerSchema, type RegisterValues } from "@/lib/validation";
import {
  PasswordPolicyHints,
  usePasswordChecks,
} from "@/components/auth/password-policy-hints";
import { ApiError } from "@/lib/api-client";

export default function RegisterPage() {
  const router = useRouter();
  const register = useAuthStore((s) => s.register);
  const login = useAuthStore((s) => s.login);

  const form = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: "", password: "", confirmPassword: "" },
  });

  // React-Compiler-friendly subscription (form.watch() trips the linter).
  const password = useWatch({ control: form.control, name: "password" }) ?? "";
  const checks = usePasswordChecks(password);
  const passedCount =
    (checks.length ? 1 : 0) + checks.rules.filter((r) => r.ok).length;
  const strength = Math.round((passedCount / 5) * 100);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function onSubmit(values: RegisterValues) {
    setSubmitError(null);
    try {
      await register({ email: values.email, password: values.password });
      // Registration returns the account; sign in right away.
      await login({ email: values.email, password: values.password });
      toast.success("Account created. A verification link was emailed to you.");
      router.replace("/");
    } catch (error) {
      setSubmitError(
        error instanceof ApiError ? error.message : "Could not create the account."
      );
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-display text-xl">Create your account</CardTitle>
        <CardDescription>
          An email with a verification link will be sent to your address.
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
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              placeholder="At least 12 characters"
              {...form.register("password")}
            />
            <Progress value={strength} className="h-1.5" />
            <PasswordPolicyHints password={password} />
            {form.formState.errors.password && (
              <p className="text-sm text-destructive">
                {form.formState.errors.password.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="confirmPassword">Confirm password</Label>
            <Input
              id="confirmPassword"
              type="password"
              autoComplete="new-password"
              {...form.register("confirmPassword")}
            />
            {form.formState.errors.confirmPassword && (
              <p className="text-sm text-destructive">
                {form.formState.errors.confirmPassword.message}
              </p>
            )}
          </div>

          {submitError && (
            <p className="text-sm text-destructive" role="alert">
              {submitError}
            </p>
          )}
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
              <UserPlus className="size-4" />
            )}
            Create account
          </Button>
          <p className="text-sm text-muted-foreground">
            Already registered?{" "}
            <Link href="/login" className="text-primary hover:underline">
              Sign in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}
