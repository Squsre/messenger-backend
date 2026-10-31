/**
 * Backend limits & configuration mirrors of app/core/security.py and the
 * documented defaults from the Messenger backend README.
 */

export const MESSAGE_MAX_LENGTH = 4000;
export const MESSAGE_PAGE_LIMIT = 50;
/** Page size for the sidebar chats/groups lists (backend max is 100). */
export const CONVERSATIONS_PAGE_LIMIT = 50;
/** Page size for contacts / blocks listings (backend max is 100). */
export const CONTACTS_PAGE_LIMIT = 100;
/** Page size for group member auto-pagination (backend max is 100). */
export const GROUP_MEMBERS_PAGE_LIMIT = 100;
/** Hard cap of member pages fetched automatically per group (2 000 rows). */
export const GROUP_MEMBERS_MAX_PAGES = 20;
/** Page size for invitation history lists (backend max is 100). */
export const INVITATIONS_PAGE_LIMIT = 50;
/** Page size for search result paging (backend max is 100). */
export const SEARCH_PAGE_LIMIT = 20;
export const SEARCH_MIN_QUERY_LENGTH = 2;
/** Page size for the group-panel invite search results. */
export const INVITE_SEARCH_PAGE_LIMIT = 10;

/** MEDIA_MAX_UPLOAD_BYTES — 10 MiB per file. */
export const MEDIA_MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
/** MEDIA_MAX_FILES_PER_REQUEST. */
export const MEDIA_MAX_FILES_PER_REQUEST = 4;
/** MEDIA_MAX_IMAGE_DIMENSION. */
export const MEDIA_MAX_IMAGE_DIMENSION = 20000;

/** Allowed upload categories mirrored from the backend allow-list. */
export const MEDIA_ALLOWED_TYPES = [
  { ext: ".jpg", mime: "image/jpeg", category: "image" },
  { ext: ".jpeg", mime: "image/jpeg", category: "image" },
  { ext: ".png", mime: "image/png", category: "image" },
  { ext: ".gif", mime: "image/gif", category: "image" },
  { ext: ".webp", mime: "image/webp", category: "image" },
  { ext: ".pdf", mime: "application/pdf", category: "document" },
  { ext: ".txt", mime: "text/plain", category: "document" },
  { ext: ".csv", mime: "text/csv", category: "document" },
] as const;

export function isAllowedUpload(file: { name: string; type: string }): {
  ok: boolean;
  reason?: string;
  category?: "image" | "document";
} {
  const name = file.name.toLowerCase();
  const match = MEDIA_ALLOWED_TYPES.find((t) => name.endsWith(t.ext));
  if (!match) {
    return { ok: false, reason: "Allowed formats: JPEG, PNG, GIF, WebP, PDF, TXT, CSV" };
  }
  return { ok: true, category: match.category };
}

/** Password policy: 12–128 chars, upper + lower + digit + special. */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

export const PASSWORD_POLICY_REGEXES: { regex: RegExp; hint: string }[] = [
  { regex: /[a-z]/, hint: "one lowercase letter" },
  { regex: /[A-Z]/, hint: "one uppercase letter" },
  { regex: /\d/, hint: "one digit" },
  { regex: /[^A-Za-z0-9]/, hint: "one special character" },
];

/** Username policy: 3–32, lowercase letters/digits/underscores, starts with letter/digit. */
export const USERNAME_REGEX = /^[a-z0-9][a-z0-9_]{2,31}$/;

/* ------------------------------ Realtime timing ----------------------------- */

/** Client-side typing frame throttle (server allows 1/s; stay safely above). */
export const TYPING_THROTTLE_MS = 1500;
/** Client-side read-frame throttle (server allows 1 per 0.5s). */
export const READ_THROTTLE_MS = 1000;
/** The server must receive the auth frame within WS_AUTH_TIMEOUT_SECONDS (5s). */
export const WS_AUTH_TIMEOUT_MS = 5000;
/** Client heartbeat interval (server pings every 30s; we ping at 25s). */
export const WS_CLIENT_PING_INTERVAL_MS = 25000;
/** If no server frame arrives in this window, the link is considered dead. */
export const WS_STALE_THRESHOLD_MS = 70000;
/** Reconnect backoff bounds. */
export const WS_RECONNECT_MIN_MS = 1000;
export const WS_RECONNECT_MAX_MS = 30000;
/** Typing indicator lifetime on the receiving side. */
export const TYPING_EXPIRY_MS = 5000;

/** Refresh presigned URLs this many seconds before their real expiry. */
export const PRESIGNED_URL_EARLY_EXPIRY_S = 30;

/** Storage keys for zustand persist. */
export const AUTH_STORAGE_KEY = "messenger-auth";
export const AUTH_COOKIE_NAME = "auth_token";
/** Matches the backend ACCESS_TOKEN_EXPIRE_MINUTES (15 min). */
export const ACCESS_TOKEN_TTL_S = 900;

/** localStorage flag the user toggles in Settings → Appearance. */
export const NOTIFICATIONS_ENABLED_KEY = "messenger-notifications-enabled";
/** localStorage key for the accent theme (Settings → Appearance). */
export const ACCENT_STORAGE_KEY = "messenger-accent";
/** DOM attribute on <html> that activates the accent palette. */
export const ACCENT_ATTR = "data-accent";
/** Uploads may take longer than ordinary JSON calls (10 MiB per file). */
export const MEDIA_UPLOAD_TIMEOUT_MS = 60_000;

/** Frontend release (shown in Settings → About next to the API banner). */
export const APP_VERSION = "2.2.1";
/** Max conversation ids probed per WS `ready` reconcile (burst guard). */
export const RECONCILE_MAX_PROBES = 12;
/** Failed deep-link probes are not retried for this long (reconnect storms). */
export const RECONCILE_RETRY_AFTER_MS = 5 * 60 * 1000;
/**
 * Extra list pages pulled automatically after `ready` when the account owns
 * more chats than the loaded pages cover (busy accounts). Bounds the burst:
 * each reconnect fetches at most 2 × READY_CATCHUP_MAX_PAGES list pages.
 */
export const READY_CATCHUP_MAX_PAGES = 4;
