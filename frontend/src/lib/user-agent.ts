/**
 * Tiny user-agent interpreter for the Sessions settings tab.
 *
 * The backend stores the raw `User-Agent` header (truncated); this module
 * turns it into a friendly "Chrome on Windows" label plus an icon hint
 * without pulling in a full ua-parser dependency. Anything unrecognized
 * falls back to a generic rendering and the raw string is never lost — the
 * session card keeps it available as a tooltip.
 *
 * Icon choices follow availability in lucide-react: Chrome and Apple have
 * brand glyphs; Firefox uses Flame (fire mascot) and Safari uses Compass
 * (its logo is a compass). Everything else renders the neutral Globe.
 */
import {
  Apple,
  Chrome,
  Compass,
  Flame,
  Globe,
  Monitor,
  Smartphone,
  Tablet,
  type LucideIcon,
} from "lucide-react";

export interface UaInfo {
  /** "Chrome 120" or null when the family cannot be detected. */
  browser: string | null;
  /** "Windows", "macOS", "Android", ... or null when unknown. */
  os: string | null;
  kind: "desktop" | "mobile" | "tablet";
  icon: LucideIcon;
  /** "Chrome on Windows" style label with sensible fallbacks. */
  label: string;
}

const GENERIC: UaInfo = {
  browser: null,
  os: null,
  kind: "desktop",
  icon: Globe,
  label: "Unknown device",
};

function firstMatch(ua: string, patterns: [RegExp, string][]): string | null {
  for (const [regex, name] of patterns) {
    if (regex.test(ua)) return name;
  }
  return null;
}

/** Browser glyph per detected family (neutral Globe when unmatched). */
function browserIcon(browser: string | null): LucideIcon | null {
  switch (browser) {
    case "Chrome":
    case "Chromium":
      return Chrome;
    case "Firefox":
      return Flame;
    case "Safari":
      return Compass;
    default:
      return null;
  }
}

/** Parse a raw User-Agent header into display info. Never throws. */
export function parseUserAgent(rawUa: string | null | undefined): UaInfo {
  const ua = (rawUa ?? "").trim();
  if (!ua) return { ...GENERIC, label: "Unknown device" };

  // ---- Browser (ordered: Chromium forks first, then engines) --------------
  const browser = firstMatch(ua, [
    [/Edg(?:A|iOS)?\//, "Edge"],
    [/OPR\/|Opera/, "Opera"],
    [/SamsungBrowser\//, "Samsung Internet"],
    [/CriOS\//, "Chrome"],
    [/FxiOS\//, "Firefox"],
    [/Firefox\//, "Firefox"],
    [/Chrome\//, "Chrome"],
    [/Chromium\//, "Chromium"],
    // Real Safari UAs place `Version/` BEFORE `Safari/` (both orders accepted;
    // Chromium UAs also carry a Safari token, but Chrome/ matched above).
    [/\bSafari\b/, "Safari"],
    [/MSIE |Trident\//, "Internet Explorer"],
  ]);

  // ---- Operating system ----------------------------------------------------
  const androidVersion = ua.match(/Android (\d+)/);
  const os = firstMatch(ua, [
    [/Windows NT 10\.0/, "Windows"],
    [/Windows NT 6\.3/, "Windows 8.1"],
    [/Windows NT 6\.2/, "Windows 8"],
    [/Windows NT 6\.1/, "Windows 7"],
    [/Windows Phone/, "Windows Phone"],
    [/CrOS/, "ChromeOS"],
    [/iPhone/, "iOS"],
    [/iPad/, "iPadOS"],
    [/Android/, androidVersion ? `Android ${androidVersion[1]}` : "Android"],
    [/Mac OS X/, "macOS"],
    [/Linux/, "Linux"],
  ]);

  // ---- Device kind ----------------------------------------------------------
  const isTablet = /iPad|Tablet|Android(?!.*Mobile)/.test(ua);
  const isMobile = /iPhone|Mobile|Windows Phone/.test(ua) && !isTablet;
  const kind: UaInfo["kind"] = isTablet ? "tablet" : isMobile ? "mobile" : "desktop";

  // ---- Icon: browser glyph wins, then Apple/phone/tablet, then Monitor -----
  const icon: LucideIcon =
    browserIcon(browser) ??
    (isTablet
      ? Tablet
      : isMobile
        ? Smartphone
        : /Mac OS X|iPhone|iPad|iOS/.test(ua)
          ? Apple
          : Monitor);

  // ---- Label ------------------------------------------------------------------
  let label: string;
  if (browser && os) label = `${browser} on ${os}`;
  else if (browser) label = browser;
  else if (os) label = os;
  else label = ua.length <= 48 ? ua : `${ua.slice(0, 45)}…`;

  if (browser === null && os === null) {
    // Recognized neither: keep the icon honest but show a trimmed raw string.
    return { browser: null, os: null, kind, icon: Globe, label };
  }
  return { browser, os, kind, icon, label };
}
