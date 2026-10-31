"use client";

/**
 * Browser notifications for incoming chat messages.
 *
 * - Works for BOTH direct and group messages: the socket store calls
 *   `notifyIncomingMessage` for every `message` frame that is not the
 *   caller's own and whose conversation is not currently visible.
 * - The Web Notifications permission is requested once the user is signed
 *   in (and can be re-requested from Settings → Appearance).
 * - The user can mute everything with the toggle in Settings → Appearance
 *   (persisted in localStorage, default: enabled).
 * - Clicking a notification focuses the app and navigates to the chat via
 *   the `messenger:navigate` event handled in the main layout.
 */

import { NOTIFICATIONS_ENABLED_KEY } from "@/lib/constants";

type ChatPathTarget = string;

export function isNotificationSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function notificationPermission(): NotificationPermission | "unsupported" {
  if (!isNotificationSupported()) return "unsupported";
  return Notification.permission;
}

/** Ask the browser for notification permission (best-effort, never throws). */
export async function requestNotificationPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!isNotificationSupported()) return "unsupported";
  if (Notification.permission !== "default") return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch {
    return Notification.permission;
  }
}

export function getNotificationsEnabled(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(NOTIFICATIONS_ENABLED_KEY) !== "off";
}

export function setNotificationsEnabled(enabled: boolean): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(NOTIFICATIONS_ENABLED_KEY, enabled ? "on" : "off");
}

interface NotificationInput {
  title: string;
  /** One-line preview (message body, "Photo", or the file name). */
  body: string;
  /** Notifications with the same tag replace each other instead of piling up. */
  tag: string;
  /** In-app path to open when the notification is clicked (e.g. `/g/3`). */
  target: ChatPathTarget;
}

export function showMessageNotification(input: NotificationInput): void {
  if (!isNotificationSupported()) return;
  if (Notification.permission !== "granted") return;
  if (!getNotificationsEnabled()) return;
  // The chat is on screen and focused — the user sees it live already.
  if (document.visibilityState === "visible" && document.hasFocus()) return;

  try {
    // `renotify` lets a replacement (same tag) alert again; not yet in every
    // TS DOM lib, so the options object is typed locally.
    const options: NotificationOptions & { renotify?: boolean } = {
      body: input.body,
      tag: input.tag,
      renotify: true,
      icon: "/logo.svg",
    };
    const notification = new Notification(input.title, options);
    notification.onclick = () => {
      window.focus();
      window.dispatchEvent(
        new CustomEvent<ChatPathTarget>("messenger:navigate", {
          detail: input.target,
        })
      );
      notification.close();
    };
  } catch {
    // Some engines construct notifications only inside service workers.
  }
}
