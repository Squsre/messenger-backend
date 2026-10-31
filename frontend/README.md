# Messenger Frontend — v2.2.1

A production-style Web Messenger frontend for the Messenger backend API (FastAPI + PostgreSQL + Redis + MinIO).

- **Stack:** Next.js 16 (App Router) · TypeScript (strict) · Zustand (+ immer, persist) · Tailwind CSS 4 · shadcn/ui · react-hook-form + zod · lucide-react · native WebSocket
- **Version:** 2.2.1
- **Typography:** local custom fonts via `next/font/local` (`public/fonts/`, SIL OFL) — **Vazirmatn** (variable, app font: Latin + Persian/Arabic), **Rajdhani** (display), **Cinzel Decorative** (brand wordmark)

## Quick start

```bash
bun install        # or npm install / pnpm install
bun run dev        # http://localhost:3000
```

## Connecting to the backend

Set the API base URL before running:

```bash
# .env.local
NEXT_PUBLIC_API_BASE_URL=https://messenger.style.dev
```

- `NEXT_PUBLIC_API_BASE_URL` — absolute base of the Messenger API (e.g. `https://messenger.style.dev`).
  When it is **not** set, the app runs in *gateway mode*: all requests go to relative
  `/api/v1/...` paths with an `XTransformPort` query parameter (for the sandbox mock service).
- `NEXT_PUBLIC_MOCK_API_PORT` — port of the mock service in gateway mode (default `3030`).

## Feature map

| Area | Details |
| ---- | ------- |
| Auth | login / register / email verify / password reset, cookie-protected routes (`auth_token`), 401 & WS-4401 auto-logout, **boot token-liveness probe (`GET /auth/me`)**, **resend verification email (`POST /auth/email/verify/request`) for unverified accounts, compact verification nudge pinned above the chat list in the sidebar header** |
| Realtime | single WebSocket at `/api/v1/ws`, auth handshake < 5 s, ping/pong heartbeat, stale-link detection, exponential-backoff reconnect, **`ready`-frame bootstrap: conversation-id reconcile + bounded list catch-up (4 extra pages per reconnect) so busy accounts see every chat after each (re)connect** |
| Deep links | **self-healing `/c/{id}` and `/g/{id}` via `GET /conversations/{id}` / `GET /groups/{id}`** — notifications and share links open even when the 50-row lists are stale; `message_count` surfaces in the chat profile panel |
| Messaging | optimistic sends (`client_message_id` UUID v4), REST fallback when the socket is down, reverse infinite scroll, day separators, typing indicators (1.5 s throttle), read watermarks with **HTTP fallback (`POST …/read`) when the socket cannot deliver the frame**, **"Unread messages" divider anchored at the first unread incoming message — the viewport opens there instead of the bottom when `unread_count > 0`** |
| Status ticks | pending → sent → delivered → read; groups show `read_count / members` |
| Media | drag & drop + picker, 10 MB / 4 files / JPEG-PNG-GIF-WebP-PDF-TXT-CSV, presigned-URL cache with early refresh, automatic authenticated-blob fallback when a presigned URL is unreachable (`<img>` onError → `/content`), **attach-to-existing-message** (`POST /media/{id}/attach`, paperclip on own text bubbles), **file-info dialog per attachment (`GET /media/{id}`)**, **document cards with a download button that survives dead presigned URLs (authenticated `/content` fallback + failure notice)** |
| Groups | create (private/public), invitations (accept/decline), **per-group outgoing invitation history with status chips + revoke (managers, `POST …/revoke` + automatic `DELETE` fallback)**, **public group discovery (`/groups/search`, race-guarded + "Load more" paging) + one-click join**, roles (owner/admin/member), promote/demote, **remove with confirmation dialog, transfer ownership with confirmation dialog (owner-only visibility)**, **leave with confirmation dialog — the button is disabled for owners with an inline "transfer ownership first" warning (409 prevention)**, **owner-only visibility editor (PATCH `visibility`)**, leave, live unread counters (with self-healing list refresh on reconnect), **member list auto-pagination for groups beyond 100 members**, **live member-panel refresh on WS join/leave/kick/role/ownership events** |
| Contacts | **dedicated `/contacts` page: add by username, search, remove, one-click chat, "Load more" paging for large address books**; add/remove also available in every profile panel; `profile_visibility: "contacts"` becomes meaningful |
| Profiles | **public profile drawer backed by `GET /users/{username}`** — opens by clicking any group member (bio, joined, last seen, visibility) with add-contact / message / block actions |
| Settings | profile (username/display name/bio), **appearance (color mode + 6 accent themes + notification toggle)**, privacy (visibility, last seen, **blocked-accounts list with unblock + "Load more" paging**), password change (fresh token adoption), active sessions (revoke one / all), **About tab: API banner (`GET /`), liveness (`GET /health`), readiness with per-dependency diagnostics (`GET /health/ready`)** |
| Notifications | desktop notifications for direct **and** group messages while the app is in the background; permission requested after sign-in (and from Settings); click-to-open navigates to the chat; per-chat tag replaces stale alerts |
| UI | 3-column desktop layout with **minimizable chat list**, mobile single-view with back navigation, dark mode (system + toggle), **accent theme palettes**, responsive profile/group panel (overlay sheet below 1024 px, static column above; **the right info panel slides in with a width transition — it never squashes the feed**), profile opens from the chat header too, **Contacts + Discover entries in the nav rail (and sidebar header on phones)**, **"Load older chats/groups" rows with Showing X of Y counters in the sidebar** |
| Chat UX | **shrink-wrap bubbles (`w-fit`, 78 % / 65 % max-width, `overflow-hidden`) — media bubbles hug the image + caption instead of stretching across the screen**, breathing room between messages, **full BiDi support: `dir="auto"` + `unicode-bidi: plaintext` on message bodies, composer and sidebar previews so Persian/Arabic text right-aligns and English left-aligns automatically**, **bubble grouping for consecutive same-sender messages (tight seams + tail corners, `rounded-2xl`)**, sticky auto-scroll, **scroll-to-bottom FAB past 300 px with unread-count badge that jumps down and marks the chat read** |
| Composer | **auto-resizing textarea (1 → 5 rows, `Enter` sends / `Shift+Enter` newline)**, **emoji picker popover beside the attachment clip**, drag & drop + validation, typing indicator, **per-chat draft persistence (localStorage, restored on chat switch, cleared on send and logout)** |
| Command palette | **global `Ctrl/Cmd+K` quick switcher** — instant local search across chats, groups and contacts, debounced race-guarded public-group search, and common actions (new chat / group, discover, invitations, contacts, settings, theme toggle) |

## Project structure

```text
src/
├── app/
│   ├── (auth)/            # login, register, verify-email, reset-password
│   ├── (main)/            # app shell + empty state, c/[id], g/[id], settings, contacts
│   ├── layout.tsx
│   └── globals.css
├── components/
│   ├── ui/                # shadcn/ui primitives
│   ├── layout/            # NavRail, ChatSidebar, InfoSidebar (profile/group/user panels)
│   ├── chat/              # ChatViewport, MessageFeed, MessageItem, Media, MediaInfoDialog, ChatInput (autosize + emoji), EmojiPicker
│   ├── dialogs/           # NewChat, CreateGroup, Invitations, DiscoverGroups, CommandPalette (Ctrl+K)
│   ├── settings/          # AboutPanel (banner / health / readiness)
│   └── common/            # UserAvatar, StatusTick, EmptyState, providers
├── hooks/                 # use-media-url, use-mobile, use-typing, use-race-guard
├── lib/                   # api-client, validation (zod), constants, utils, media-url-cache, notifications, draft-store
├── public/fonts/          # Vazirmatn (variable) + Rajdhani + Cinzel Decorative (SIL OFL)
├── stores/                # auth, conversation, message, socket, contacts, ui (+ reset registry)
├── types/                 # api.ts (from openapi.json), ws.ts (frame contract)
└── proxy.ts               # cookie route guard (Next 16 middleware convention)
```

## Backend contract notes

- Access tokens live 15 minutes; any HTTP `401` or WS close `4401/4403` purges the session and redirects to `/login?expired=1`.
- The WebSocket auth frame is sent as the first frame within 5 s; server `ping` frames are answered with `pong` immediately; the client also pings every 25 s and force-recycles links silent for > 70 s.
- Typing frames are throttled client-side to one `true` per 1.5 s (`is_typing:false` stop frames are never throttled); read frames are throttled to 1 s and never repeat an older `last_message_id`.
- `delivered`/`read` events are watermarks: every message with `id <= last_message_id` is updated in one pass.
- Password policy enforced in forms: 12–128 chars with upper, lower, digit and special character.
- After a socket reconnect both list endpoints are refreshed so server-side unread counters resync; a message frame for an unknown conversation/group schedules a debounced list refresh instead of being dropped.
- After every (re)connect the `ready` id list drives two bounded reconcilers: up to 12 detail probes for stray ids and up to 4 extra offset pages of both lists for busy accounts; all merges dedupe by id.
- Every collection endpoint pages with `limit`/`offset`; merges dedupe by id and keep server `total` so "Load older …" rows appear exactly while the loaded window is smaller than the server total.

## Endpoint coverage

See `docs/UNIMPLEMENTED_ENDPOINTS.md` for the full audit: what v1.6.0 added
(offset pagination for every collection — chats, groups, members, invitations,
contacts, blocks and search — race-guarded searches, invitation history
tabs, document download fallback, busy-account `ready` catch-up), what
v1.7.0 added on top (media bubble shrink-wrap fix, full BiDi/RTL message
support, autosizing composer + emoji picker, local custom fonts, per-chat
drafts, the `Ctrl+K` command palette, and the unread-badged scroll-to-bottom
FAB), what v1.8.0 hardened in the Category-1 audit (owner-only group
`visibility` editor, confirmation dialogs for leave / kick / ownership
transfer, the owner leave-guard, WS-driven ownership state repair and live
member-panel refreshes) and what v1.9.0 hardened in the Category-2 audit
(chat-wide drag & drop with overlay, a real XHR upload progress bar, orphan
cleanup of unattached uploads on chip removal / chat switch / unmount,
friendly 413/415/503/429 upload toasts, per-mode presigned-URL inflight
dedup and an Escape-closable image lightbox).
