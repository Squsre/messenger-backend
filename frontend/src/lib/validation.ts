/**
 * Zod schemas + react-hook-form resolvers for every client form.
 * Mirrors the backend validation policy (app/core/security.py).
 */
import { z } from "zod";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  PASSWORD_POLICY_REGEXES,
  SEARCH_MIN_QUERY_LENGTH,
  USERNAME_REGEX,
} from "@/lib/constants";

/* ------------------------------- Auth forms -------------------------------- */

export const loginSchema = z.object({
  email: z.email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});
export type LoginValues = z.infer<typeof loginSchema>;

const passwordPolicy = z
  .string()
  .min(PASSWORD_MIN_LENGTH, `At least ${PASSWORD_MIN_LENGTH} characters.`)
  .max(PASSWORD_MAX_LENGTH, `At most ${PASSWORD_MAX_LENGTH} characters.`)
  .refine((value) => PASSWORD_POLICY_REGEXES.every(({ regex }) => regex.test(value)), {
    message:
      "Must include an uppercase letter, a lowercase letter, a digit and a special character.",
  });

export const registerSchema = z
  .object({
    email: z.email("Enter a valid email address."),
    password: passwordPolicy,
    confirmPassword: z.string().min(1, "Repeat your password."),
  })
  .refine((values) => values.password === values.confirmPassword, {
    path: ["confirmPassword"],
    message: "Passwords do not match.",
  });
export type RegisterValues = z.infer<typeof registerSchema>;

export const verifyEmailSchema = z.object({
  token: z.string().min(10, "Paste the token from the verification link."),
});
export type VerifyEmailValues = z.infer<typeof verifyEmailSchema>;

export const resetRequestSchema = z.object({
  email: z.email("Enter a valid email address."),
});
export type ResetRequestValues = z.infer<typeof resetRequestSchema>;

export const resetConfirmSchema = z
  .object({
    token: z.string().min(10, "Paste the token from the reset link."),
    newPassword: passwordPolicy,
    confirmPassword: z.string().min(1, "Repeat the new password."),
  })
  .refine((values) => values.newPassword === values.confirmPassword, {
    path: ["confirmPassword"],
    message: "Passwords do not match.",
  });
export type ResetConfirmValues = z.infer<typeof resetConfirmSchema>;

export const changePasswordSchema = z
  .object({
    currentPassword: z.string().min(1, "Enter your current password."),
    newPassword: passwordPolicy,
    confirmPassword: z.string().min(1, "Repeat the new password."),
  })
  .refine((values) => values.newPassword !== values.currentPassword, {
    path: ["newPassword"],
    message: "The new password must be different.",
  })
  .refine((values) => values.newPassword === values.confirmPassword, {
    path: ["confirmPassword"],
    message: "Passwords do not match.",
  });
export type ChangePasswordValues = z.infer<typeof changePasswordSchema>;

/* ------------------------------ Profile forms ------------------------------ */

export const profileSchema = z.object({
  username: z
    .string()
    .regex(USERNAME_REGEX, "3-32 chars: a-z, 0-9, underscore; starts with a letter or digit.")
    .optional(),
  displayName: z.string().max(64, "At most 64 characters.").optional(),
  bio: z.string().max(280, "At most 280 characters.").optional(),
  profileVisibility: z.enum(["public", "contacts", "private"]),
  showLastSeen: z.boolean(),
});
export type ProfileValues = z.infer<typeof profileSchema>;

/* -------------------------------- Chat forms ------------------------------- */

export const newChatSchema = z.object({
  username: z
    .string()
    .min(SEARCH_MIN_QUERY_LENGTH, `At least ${SEARCH_MIN_QUERY_LENGTH} characters.`)
    .max(32, "At most 32 characters."),
});
export type NewChatValues = z.infer<typeof newChatSchema>;

export const createGroupSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Give the group a name.")
    .max(64, "At most 64 characters."),
  description: z.string().max(280, "At most 280 characters.").optional(),
  visibility: z.enum(["private", "public"]),
});
export type CreateGroupValues = z.infer<typeof createGroupSchema>;

export const sendMessageSchema = z.object({
  body: z.string().max(4000, "Messages are limited to 4000 characters."),
});
export type SendMessageValues = z.infer<typeof sendMessageSchema>;
