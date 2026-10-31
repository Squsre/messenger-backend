"use client";

/**
 * Settings: profile (username/display name/bio), privacy (visibility +
 * last seen), password change and active device sessions.
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { useForm, useWatch } from "react-hook-form";
import { useTheme } from "next-themes";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { differenceInSeconds, format } from "date-fns";
import {
  ArrowLeft,
  Ban,
  BellRing,
  ChevronDown,
  Eye,
  EyeOff,
  Globe,
  KeyRound,
  Loader2,
  LogOut,
  Monitor,
  MonitorSmartphone,
  Moon,
  Palette,
  Save,
  Server,
  ShieldCheck,
  ShieldOff,
  Sun,
  Trash2,
  UserRound,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { UserAvatar } from "@/components/common/user-avatar";
import { AboutPanel } from "@/components/settings/about-panel";
import { UnverifiedEmailBanner } from "@/components/auth/unverified-email-banner";
import { PasswordPolicyHints } from "@/components/auth/password-policy-hints";
import {
  ACCENT_THEMES,
  applyAccent,
  readSavedAccent,
  type AccentId,
} from "@/components/common/app-providers";
import {
  getNotificationsEnabled,
  notificationPermission,
  requestNotificationPermission,
  setNotificationsEnabled,
} from "@/lib/notifications";
import { ACCENT_STORAGE_KEY } from "@/lib/constants";
import { useAuthStore } from "@/stores/use-auth-store";
import { useBlocksStore } from "@/stores/use-blocks-store";
import { apiClient, ApiError } from "@/lib/api-client";
import {
  changePasswordSchema,
  profileSchema,
  type ChangePasswordValues,
  type ProfileValues,
} from "@/lib/validation";
import type {
  ProfileUpdate,
  SessionList,
  SessionRead,
  TokenResponse,
  UserPrivate,
} from "@/types/api";
import { displayNameOf, formatListTimestamp } from "@/lib/utils";
import { parseUserAgent } from "@/lib/user-agent";

export default function SettingsPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-background px-4">
        <Button asChild variant="ghost" size="icon" className="md:hidden" aria-label="Back">
          <Link href="/">
            <ArrowLeft className="size-5" />
          </Link>
        </Button>
        <div>
          <h1 className="text-base font-semibold leading-tight">Settings</h1>
          <p className="text-xs text-muted-foreground">
            Profile, appearance, notifications, privacy, blocks and devices
          </p>
        </div>
      </header>

      <Tabs defaultValue="profile" className="chat-scroll flex-1 overflow-y-auto p-4 md:p-6">
        <TabsList>
          <TabsTrigger value="profile" className="gap-1.5">
            <UserRound className="size-4" /> Profile
          </TabsTrigger>
          <TabsTrigger value="appearance" className="gap-1.5">
            <Palette className="size-4" /> Appearance
          </TabsTrigger>
          <TabsTrigger value="privacy" className="gap-1.5" aria-label="Privacy tab">
            <ShieldCheck className="size-4" /> Privacy
          </TabsTrigger>
          <TabsTrigger value="security" className="gap-1.5">
            <KeyRound className="size-4" /> Password
          </TabsTrigger>
          <TabsTrigger value="sessions" className="gap-1.5">
            <MonitorSmartphone className="size-4" /> Sessions
          </TabsTrigger>
          <TabsTrigger value="about" className="gap-1.5">
            <Server className="size-4" /> About
          </TabsTrigger>
        </TabsList>

        <TabsContent value="profile" className="mt-4 max-w-xl">
          <ProfilePanel />
        </TabsContent>
        <TabsContent value="appearance" className="mt-4 max-w-xl">
          <AppearancePanel />
        </TabsContent>
        <TabsContent value="privacy" className="mt-4 max-w-xl">
          <PrivacyPanel />
        </TabsContent>
        <TabsContent value="security" className="mt-4 max-w-xl">
          <PasswordPanel />
        </TabsContent>
        <TabsContent value="sessions" className="mt-4 max-w-xl">
          <SessionsPanel />
        </TabsContent>
        <TabsContent value="about" className="mt-4 max-w-xl">
          <AboutPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}

/* --------------------------------- Profile ---------------------------------- */

function ProfilePanel() {
  const user = useAuthStore((s) => s.user);
  const form = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    values: {
      username: user?.username ?? "",
      displayName: user?.display_name ?? "",
      bio: user?.bio ?? "",
      profileVisibility: user?.profile_visibility ?? "public",
      showLastSeen: user?.show_last_seen ?? true,
    },
  });

  async function onSubmit(values: ProfileValues) {
    const previous = useAuthStore.getState().user;
    // Optimistic store update: badges, headers and the email badge reflect
    // the edit instantly; the server response reconciles right after (and a
    // failure rolls the snapshot back).
    if (previous) {
      useAuthStore.setState({
        user: {
          ...previous,
          username: values.username || previous.username,
          display_name: values.displayName?.trim() || null,
          bio: values.bio?.trim() || null,
          profile_visibility: values.profileVisibility,
          show_last_seen: values.showLastSeen,
        },
      });
    }
    try {
      // extra="forbid" on the backend: only the five editable fields may be
      // sent — id/email/is_active/token_version/email_verified/avatar_key
      // would 422 the whole PATCH. An empty display name or bio is sent as
      // null, which clears the field server-side.
      const payload: ProfileUpdate = {
        display_name: values.displayName?.trim() || null,
        bio: values.bio?.trim() || null,
        profile_visibility: values.profileVisibility,
        show_last_seen: values.showLastSeen,
      };
      // Only send `username` when it actually changed — resubmitting the
      // current handle is a needless 409 risk.
      if (values.username && values.username !== previous?.username) {
        payload.username = values.username;
      }
      const updated = await apiClient.patch<UserPrivate>("/api/v1/users/me", payload);
      useAuthStore.setState({ user: updated });
      toast.success("Profile saved.");
    } catch (error) {
      if (previous) useAuthStore.setState({ user: previous });
      if (error instanceof ApiError && error.status === 409) {
        // Username conflicts surface inline on the field, not as a toast.
        form.setError("username", {
          type: "server",
          message: "That username is already taken. Try another one.",
        });
        return;
      }
      toast.error(
        error instanceof ApiError ? error.message : "Could not save the profile."
      );
    }
  }

  if (!user) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-6">
      {!user.email_verified && <UnverifiedEmailBanner email={user.email} />}
      <div className="flex items-center gap-4">
        <UserAvatar
          name={user.display_name}
          username={user.username}
          className="size-16 text-lg"
        />
        <div>
          <p className="font-medium">{displayNameOf(user.display_name, user.username)}</p>
          <p className="text-sm text-muted-foreground">{user.email}</p>
          <Badge variant={user.email_verified ? "secondary" : "outline"} className="mt-1">
            {user.email_verified ? "Email verified" : "Email not verified"}
          </Badge>
        </div>
      </div>

      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="username">Username</Label>
          <Input id="username" {...form.register("username")} />
          <p className="text-xs text-muted-foreground">
            3–32 characters: lowercase letters, digits, underscores; starts with a
            letter or digit.
          </p>
          {form.formState.errors.username && (
            <p className="text-sm text-destructive">
              {form.formState.errors.username.message}
            </p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="displayName">Display name</Label>
          <Input id="displayName" maxLength={64} {...form.register("displayName")} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="bio">Bio</Label>
          <Textarea id="bio" rows={3} maxLength={280} {...form.register("bio")} />
        </div>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          Save profile
        </Button>
      </form>
    </div>
  );
}

/* -------------------------------- Appearance -------------------------------- */

const MODES = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

function AppearancePanel() {
  const { theme, setTheme } = useTheme();
  const [accent, setAccent] = useState<AccentId>("default");
  const [notifEnabled, setNotifEnabled] = useState(true);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");

  // localStorage is only available after mount (avoid hydration mismatch);
  // values land in a callback so the first paint stays server-consistent.
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      setAccent(readSavedAccent());
      setNotifEnabled(getNotificationsEnabled());
      setPermission(notificationPermission());
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  function pickAccent(next: AccentId) {
    setAccent(next);
    applyAccent(next);
    try {
      window.localStorage.setItem(ACCENT_STORAGE_KEY, next);
    } catch {
      // Private mode — the palette still applies for this session.
    }
  }

  async function toggleNotifications(enabled: boolean) {
    setNotifEnabled(enabled);
    setNotificationsEnabled(enabled);
    if (enabled) {
      const result = await requestNotificationPermission();
      setPermission(result);
      if (result === "denied") {
        toast.error("Notifications are blocked in the browser site settings.");
      }
    }
  }

  const permissionLabel =
    permission === "granted"
      ? "Browser permission granted."
      : permission === "denied"
        ? "Blocked — allow notifications for this site in the browser settings."
        : permission === "unsupported"
          ? "This browser does not support notifications."
          : "Permission not requested yet.";

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Label>Color mode</Label>
        <div className="grid grid-cols-3 gap-2">
          {MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              onClick={() => setTheme(mode.value)}
              className={`flex flex-col items-center gap-1.5 rounded-lg border p-3 text-sm transition-colors ${
                theme === mode.value
                  ? "border-primary bg-primary/5 text-foreground"
                  : "text-muted-foreground hover:bg-accent"
              }`}
              aria-pressed={theme === mode.value}
            >
              <mode.icon className="size-4" />
              {mode.label}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <Label>Messenger theme</Label>
        <p className="text-xs text-muted-foreground">
          The accent color of bubbles, buttons and highlights. Works with both color modes.
        </p>
        <div className="grid grid-cols-3 gap-2">
          {ACCENT_THEMES.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => pickAccent(option.id)}
              className={`flex items-center gap-2 rounded-lg border p-2.5 text-sm transition-colors ${
                accent === option.id
                  ? "border-primary bg-primary/5"
                  : "hover:bg-accent"
              }`}
              aria-pressed={accent === option.id}
            >
              <span
                className="size-5 shrink-0 rounded-full border border-black/10"
                style={{ background: option.swatch }}
                aria-hidden
              />
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <Label className="flex items-center gap-2">
          <BellRing className="size-4" /> Notifications
        </Label>
        <div className="flex items-center justify-between rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">Desktop notifications</p>
            <p className="text-xs text-muted-foreground">
              {permissionLabel} A notification appears for new direct and group
              messages while the app is in the background.
            </p>
          </div>
          <Switch
            checked={notifEnabled}
            onCheckedChange={(next) => void toggleNotifications(next)}
            aria-label="Toggle desktop notifications"
          />
        </div>
        {permission === "default" && (
          <Button
            variant="outline"
            size="sm"
            onClick={async () => setPermission(await requestNotificationPermission())}
          >
            <BellRing className="size-4" /> Allow notifications
          </Button>
        )}
      </div>
    </div>
  );
}

/* --------------------------------- Privacy ---------------------------------- */

function PrivacyPanel() {
  const user = useAuthStore((s) => s.user);
  const [visibility, setVisibility] = useState(user?.profile_visibility ?? "public");
  const [showLastSeen, setShowLastSeen] = useState(user?.show_last_seen ?? true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (user) {
      setVisibility(user.profile_visibility);
      setShowLastSeen(user.show_last_seen);
    }
  }, [user]);

  async function save() {
    setSaving(true);
    try {
      const updated = await apiClient.patch<UserPrivate>(
        "/api/v1/users/me/privacy",
        { profile_visibility: visibility, show_last_seen: showLastSeen }
      );
      useAuthStore.setState({ user: updated });
      toast.success("Privacy settings saved.");
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not save privacy."
      );
    } finally {
      setSaving(false);
    }
  }

  if (!user) return <Skeleton className="h-48 w-full" />;

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Label>Who can see your profile?</Label>
        <div className="grid gap-2">
          {(
            [
              { value: "public", label: "Public", hint: "Any authenticated account." },
              { value: "contacts", label: "Contacts", hint: "Only people who added you." },
              { value: "private", label: "Private", hint: "Nobody but you (404 for others)." },
            ] as const
          ).map((option) => (
            <label
              key={option.value}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                visibility === option.value ? "border-primary bg-primary/5" : "hover:bg-accent"
              }`}
            >
              <input
                type="radio"
                name="visibility"
                className="mt-1"
                checked={visibility === option.value}
                onChange={() => setVisibility(option.value)}
              />
              <span>
                <span className="block text-sm font-medium">{option.label}</span>
                <span className="block text-xs text-muted-foreground">{option.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="flex items-center justify-between rounded-lg border p-3">
        <div>
          <Label className="text-sm font-medium">Show last seen</Label>
          <p className="text-xs text-muted-foreground">
            When disabled, nobody except you sees your activity time.
          </p>
        </div>
        <Switch checked={showLastSeen} onCheckedChange={setShowLastSeen} />
      </div>

      <Button onClick={save} disabled={saving}>
        {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
        Save privacy
      </Button>

      <BlockedAccountsSection />
    </div>
  );
}

/* ------------------------------ Blocked accounts ----------------------------- */

/**
 * Settings → Privacy → Blocked accounts. Backed by the shared blocks store
 * so blocks/unblocks made anywhere (profile drawer, chat header menu,
 * composer) are reflected here instantly — and vice versa.
 */
function BlockedAccountsSection() {
  const blocked = useBlocksStore((s) => s.blocked);
  const total = useBlocksStore((s) => s.total);
  const loaded = useBlocksStore((s) => s.loaded);
  const loading = useBlocksStore((s) => s.loading);
  const nextOffset = useBlocksStore((s) => s.nextOffset);
  const loadingMore = useBlocksStore((s) => s.loadingMore);
  const busy = useBlocksStore((s) => s.busyUsername);

  useEffect(() => {
    void useBlocksStore.getState().load();
  }, []);

  async function unblock(username: string) {
    try {
      await useBlocksStore.getState().unblock(username);
      toast.info(`Unblocked @${username}.`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unblock failed.");
    }
  }

  return (
    <div className="space-y-2">
      <Label className="flex items-center gap-2">
        <Ban className="size-4" /> Blocked accounts
      </Label>
      <p className="text-xs text-muted-foreground">
        Blocks stop new messages in both directions. Either side can remove the
        block; existing history stays readable.
      </p>
      {!loaded && loading ? (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : blocked.length === 0 ? (
        <p className="rounded-lg border p-3 text-sm text-muted-foreground">
          You have not blocked anyone.
        </p>
      ) : (
        <div className="space-y-2">
          {blocked.map((user) => (
            <div
              key={user.username}
              className="flex items-center gap-3 rounded-lg border p-3"
            >
              <UserAvatar
                name={user.display_name}
                username={user.username}
                className="size-9"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">
                  {displayNameOf(user.display_name, user.username)}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  @{user.username}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={busy !== null}
                onClick={() => unblock(user.username)}
                aria-label={`Unblock ${user.username}`}
              >
                {busy === user.username ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Ban className="size-4" />
                )}
                Unblock
              </Button>
            </div>
          ))}

          {/* Blocklist paging: beyond the first 100 entries. */}
          {total !== null && nextOffset < total && (
            <div className="flex justify-center pt-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-8 gap-1.5 text-xs text-muted-foreground"
                disabled={loadingMore}
                onClick={() => void useBlocksStore.getState().loadMore()}
              >
                {loadingMore ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <ChevronDown className="size-3.5" />
                )}
                Load more ({blocked.length}/{total})
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* --------------------------------- Password --------------------------------- */

function PasswordPanel() {
  // Per-field visibility toggles (Eye / EyeOff) for all three inputs.
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const form = useForm<ChangePasswordValues>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { currentPassword: "", newPassword: "", confirmPassword: "" },
  });
  // React-Compiler-friendly subscription (form.watch() trips the linter).
  const newPassword = useWatch({ control: form.control, name: "newPassword" }) ?? "";

  async function onSubmit(values: ChangePasswordValues) {
    try {
      const token = await apiClient.post<TokenResponse>(
        "/api/v1/auth/password/change",
        {
          current_password: values.currentPassword,
          new_password: values.newPassword,
        }
      );
      // The backend returns a fresh token for THIS device; everyone else is
      // signed out by the token_version bump. adoptToken() updates both the
      // persisted store and the middleware cookie, so this session survives.
      useAuthStore.getState().adoptToken(token);
      form.reset();
      setShowCurrent(false);
      setShowNew(false);
      setShowConfirm(false);
      toast.success("Password changed. Other devices were signed out.");
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Could not change the password."
      );
    }
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="currentPassword">Current password</Label>
        <div className="relative">
          <Input
            id="currentPassword"
            type={showCurrent ? "text" : "password"}
            autoComplete="current-password"
            className="pr-10"
            {...form.register("currentPassword")}
          />
          <button
            type="button"
            tabIndex={-1}
            aria-label={showCurrent ? "Hide current password" : "Show current password"}
            onClick={() => setShowCurrent((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
          >
            {showCurrent ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        </div>
        {form.formState.errors.currentPassword && (
          <p className="text-sm text-destructive">
            {form.formState.errors.currentPassword.message}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="newPassword">New password</Label>
        <div className="relative">
          <Input
            id="newPassword"
            type={showNew ? "text" : "password"}
            autoComplete="new-password"
            className="pr-10"
            {...form.register("newPassword")}
          />
          <button
            type="button"
            tabIndex={-1}
            aria-label={showNew ? "Hide new password" : "Show new password"}
            onClick={() => setShowNew((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
          >
            {showNew ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        </div>
        <PasswordPolicyHints password={newPassword} />
        <p className="text-xs text-muted-foreground">
          Changing it signs out every other device; this one keeps its session.
        </p>
        {form.formState.errors.newPassword && (
          <p className="text-sm text-destructive">
            {form.formState.errors.newPassword.message}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="confirmNew">Confirm new password</Label>
        <div className="relative">
          <Input
            id="confirmNew"
            type={showConfirm ? "text" : "password"}
            autoComplete="new-password"
            className="pr-10"
            {...form.register("confirmPassword")}
          />
          <button
            type="button"
            tabIndex={-1}
            aria-label={showConfirm ? "Hide confirmation" : "Show confirmation"}
            onClick={() => setShowConfirm((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
          >
            {showConfirm ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        </div>
        {form.formState.errors.confirmPassword && (
          <p className="text-sm text-destructive">
            {form.formState.errors.confirmPassword.message}
          </p>
        )}
      </div>
      <Button type="submit" disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <KeyRound className="size-4" />
        )}
        Change password
      </Button>
    </form>
  );
}

/* --------------------------------- Sessions --------------------------------- */

/** "just now" / "5 min ago" / "3 h ago" / "12 Jan, 14:32" for a session. */
function sessionActivity(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  const seconds = differenceInSeconds(new Date(), date);
  if (seconds < 0) return "just now";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return format(date, "d MMM, HH:mm");
}

function SessionsPanel() {
  const [sessions, setSessions] = useState<SessionRead[] | null>(null);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState<number | "all" | null>(null);
  /** Non-current session waiting for the revoke confirmation dialog. */
  const [confirmRevoke, setConfirmRevoke] = useState<SessionRead | null>(null);
  /** Whether the "sign out everywhere" warning dialog is open. */
  const [confirmAllOpen, setConfirmAllOpen] = useState(false);

  async function load() {
    try {
      const list = await apiClient.get<SessionList>("/api/v1/auth/sessions");
      setSessions(list.items);
      setTotal(list.total);
    } catch {
      setSessions([]);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function revoke(id: number) {
    setBusy(id);
    try {
      await apiClient.delete(`/api/v1/auth/sessions/${id}`);
      setSessions((prev) => prev?.filter((s) => s.id !== id) ?? null);
      setTotal((t) => Math.max(0, t - 1));
      toast.info("Device signed out. Its token was revoked server-side.");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Revoke failed.");
      // The row may be stale (already expired server-side) — resync.
      void load();
    } finally {
      setBusy(null);
      setConfirmRevoke(null);
    }
  }

  async function revokeAll() {
    setBusy("all");
    try {
      await apiClient.post("/api/v1/auth/logout/all");
      // This signs out the current device as well — the API invalidated our
      // token (token_version bump), so do a clean local logout.
      useAuthStore.getState().forceLogout("revoked");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Revoke failed.");
      setBusy(null);
      setConfirmAllOpen(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Every login records a device session. Revoking a device invalidates its
        access token through the server-side denylist.
        {total > 0 && ` You currently have ${total} active ${total === 1 ? "device" : "devices"}.`}
      </p>
      <Separator />
      {sessions === null ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : sessions.length === 0 ? (
        <p className="text-sm text-muted-foreground">No active sessions.</p>
      ) : (
        <div className="space-y-2">
          {sessions.map((session) => {
            const ua = parseUserAgent(session.user_agent);
            return (
              <div
                key={session.id}
                className={`flex items-center gap-3 rounded-lg border p-3 ${
                  session.current
                    ? "border-primary/50 bg-primary/5"
                    : ""
                }`}
              >
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  <ua.icon className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{ua.label}</p>
                  <p
                    className="truncate text-xs text-muted-foreground"
                    title={session.user_agent ?? undefined}
                  >
                    <Globe className="mr-1 inline size-3 align-[-1px]" aria-hidden />
                    {session.ip_address ?? "Unknown IP"} · Active{" "}
                    {sessionActivity(session.last_active_at)}
                  </p>
                  <p className="text-xs text-muted-foreground/80">
                    Signed in {formatListTimestamp(session.created_at)}
                  </p>
                </div>
                {session.current ? (
                  <Badge className="shrink-0 gap-1">
                    <ShieldCheck className="size-3" aria-hidden />
                    This device
                  </Badge>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0 text-destructive hover:bg-destructive/10 hover:text-destructive"
                    disabled={busy !== null}
                    onClick={() => setConfirmRevoke(session)}
                    aria-label={`Sign out ${ua.label}`}
                  >
                    {busy === session.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <LogOut className="size-4" />
                    )}
                    Sign out
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}

      <Button
        variant="destructive"
        onClick={() => setConfirmAllOpen(true)}
        disabled={busy !== null}
      >
        {busy === "all" ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <ShieldOff className="size-4" />
        )}
        Sign out of all devices
      </Button>

      {/* Revoke one device — confirmation guard per the security checklist. */}
      <AlertDialog
        open={confirmRevoke !== null}
        onOpenChange={(open) => {
          if (!open && busy === null) setConfirmRevoke(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Sign out this device?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmRevoke && (
                <>
                  {parseUserAgent(confirmRevoke.user_agent).label} (
                  {confirmRevoke.ip_address ?? "unknown IP"}) will be signed out
                  immediately. Its access token is rejected by the server
                  denylist on the next request — this cannot be undone from
                  here.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={busy !== null}
              onClick={(event) => {
                event.preventDefault();
                if (confirmRevoke) void revoke(confirmRevoke.id);
              }}
            >
              {busy === confirmRevoke?.id ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <LogOut className="size-4" />
              )}
              Sign out device
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Logout-all — destructive guard: invalidates EVERY session. */}
      <AlertDialog open={confirmAllOpen} onOpenChange={setConfirmAllOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Log out of all devices?</AlertDialogTitle>
            <AlertDialogDescription>
              Every active session — including this browser — will be
              terminated and all outstanding access tokens invalidated. You
              will need to sign in again on each device.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={busy !== null}
              onClick={(event) => {
                event.preventDefault();
                void revokeAll();
              }}
            >
              {busy === "all" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Trash2 className="size-4" />
              )}
              Terminate all sessions
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
