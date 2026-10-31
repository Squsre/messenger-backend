import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import { format, isToday, isYesterday, differenceInSeconds } from "date-fns"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/* ------------------------------ Date formatters ----------------------------- */

/** "14:32" for today, "Yesterday 14:32", else "12 Jan, 14:32". */
export function formatListTimestamp(iso: string | null | undefined): string {
  if (!iso) return ""
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  if (isToday(date)) return format(date, "HH:mm")
  if (isYesterday(date)) return `Yesterday ${format(date, "HH:mm")}`
  return format(date, "d MMM")
}

/** "14:32" — used on message bubbles. */
export function formatClock(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  return format(date, "HH:mm")
}

/** Day divider label: "Today" / "Yesterday" / "12 January 2026". */
export function formatDayDivider(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  if (isToday(date)) return "Today"
  if (isYesterday(date)) return "Yesterday"
  return format(date, "d MMMM yyyy")
}

/** Relative last-seen: "active just now", "active 5 min ago", "active 3 h ago". */
export function formatLastSeen(iso: string | null | undefined): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const seconds = differenceInSeconds(new Date(), date)
  if (seconds < 60) return "active just now"
  if (seconds < 3600) return `active ${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `active ${Math.floor(seconds / 3600)} h ago`
  if (isToday(date)) return `active today at ${format(date, "HH:mm")}`
  return `active ${format(date, "d MMM")}`
}

/* ------------------------------ Byte formatter ------------------------------ */

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ""
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB"]
  let value = bytes
  let unit = "B"
  for (const next of units) {
    if (value < 1024) break
    value /= 1024
    unit = next
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${unit}`
}

/* ------------------------------ Avatar helpers ------------------------------ */

export function initialsOf(name: string | null | undefined, fallback: string): string {
  const source = (name ?? "").trim() || fallback
  const parts = source.split(/[\s._-]+/).filter(Boolean)
  if (parts.length === 0) return "?"
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase()
  return (parts[0]![0]! + parts[1]![0]!).toUpperCase()
}

/** Deterministic pastel avatar background from a string seed. */
export function avatarTone(seed: string): string {
  const tones = [
    "bg-rose-200 text-rose-900 dark:bg-rose-900/60 dark:text-rose-100",
    "bg-amber-200 text-amber-900 dark:bg-amber-900/60 dark:text-amber-100",
    "bg-emerald-200 text-emerald-900 dark:bg-emerald-900/60 dark:text-emerald-100",
    "bg-teal-200 text-teal-900 dark:bg-teal-900/60 dark:text-teal-100",
    "bg-cyan-200 text-cyan-900 dark:bg-cyan-900/60 dark:text-cyan-100",
    "bg-purple-200 text-purple-900 dark:bg-purple-900/60 dark:text-purple-100",
    "bg-fuchsia-200 text-fuchsia-900 dark:bg-fuchsia-900/60 dark:text-fuchsia-100",
    "bg-lime-200 text-lime-900 dark:bg-lime-900/60 dark:text-lime-100",
  ]
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0
  }
  return tones[hash % tones.length]!
}

export function displayNameOf(
  displayName: string | null | undefined,
  username: string
): string {
  const trimmed = (displayName ?? "").trim()
  return trimmed.length > 0 ? trimmed : username
}

/** UUID v4 with a graceful fallback for non-secure contexts. */
export function uuidv4(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  // RFC4122-ish fallback (non-crypto) — only used on legacy non-secure origins.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === "x" ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}
