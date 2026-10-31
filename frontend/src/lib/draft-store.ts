"use client";

/**
 * Per-conversation message drafts, persisted in localStorage.
 *
 * Keyed by `${chatType}:${conversationId}`. Writes are debounced so typing
 * stays cheap; the value lives in a module-level cache so reads during a
 * chat switch are synchronous and never touch the disk more than needed.
 *
 * Drafts are personal data — `clearAllDrafts()` runs on logout/forced
 * logout so the next account on a shared machine starts clean.
 */

const STORAGE_KEY = "messenger-drafts-v1";
const WRITE_DEBOUNCE_MS = 400;

type DraftMap = Record<string, string>;

let cache: DraftMap | null = null;
let writeTimer: ReturnType<typeof setTimeout> | null = null;

function readAll(): DraftMap {
  if (cache) return cache;
  cache = {};
  if (typeof window === "undefined") return cache;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
          if (typeof value === "string") cache[key] = value;
        }
      }
    }
  } catch {
    // Corrupted or unavailable storage — drafts are best-effort.
  }
  return cache;
}

function flush(): void {
  writeTimer = null;
  if (typeof window === "undefined" || !cache) return;
  try {
    const hasContent = Object.keys(cache).length > 0;
    if (hasContent) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Quota / private mode — drafts are best-effort.
  }
}

/** Restore the draft saved for a conversation ("" when none). */
export function loadDraft(key: string): string {
  return readAll()[key] ?? "";
}

/** Persist (or clear, when `text` is empty) the draft for a conversation. */
export function saveDraft(key: string, text: string): void {
  const all = readAll();
  if (text) {
    if (all[key] === text) return;
    all[key] = text;
  } else {
    if (!(key in all)) return;
    delete all[key];
  }
  if (writeTimer) clearTimeout(writeTimer);
  writeTimer = setTimeout(flush, WRITE_DEBOUNCE_MS);
}

/** Drop one draft immediately (used on send). */
export function clearDraft(key: string): void {
  const all = readAll();
  if (!(key in all)) return;
  delete all[key];
  if (writeTimer) {
    clearTimeout(writeTimer);
    writeTimer = null;
  }
  flush();
}

/** Wipe every draft — called on logout / forced logout. */
export function clearAllDrafts(): void {
  cache = {};
  if (writeTimer) {
    clearTimeout(writeTimer);
    writeTimer = null;
  }
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore storage failures.
  }
}
