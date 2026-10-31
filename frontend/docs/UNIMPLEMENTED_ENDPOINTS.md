# Backend Endpoint Audit — v1.9.0 (Category 2: Media, Files & MinIO)

Audit of `upload/openapi.json` + `upload/README.md` against the frontend call
sites (`src/**`) after the v1.9.0 changes.

**Result: all 6 Media endpoints were already integrated end-to-end; the
Category-2 audit verified every contract detail and closed 8 edge-case/UI
gaps.** Full REST + pagination coverage retained (all 59 operations keep at
least one call site).

## Delivered in v1.9.0 (Category-2 audit)

| # | Change | Mechanics | Files |
|---|--------|-----------|-------|
| 1 | **Chat-wide drag & drop** — files can now be dropped anywhere over the chat surface (header + history), not just the input strip; a dashed overlay with the limits shows while dragging | `dragenter`/`dragleave` depth counter keeps the overlay stable across child boundaries; drops publish to a tiny module bus and the composer runs its usual validation + upload pipeline | `chat-viewport.tsx`, `media-drop-bus.ts` (new), `chat-input.tsx` |
| 2 | **Real upload progress bar** — determinate 0–100 % indicator under the attachment chips (`fetch` cannot observe upload progress, so uploads use XHR with the same envelope/401/timeout handling) | `uploadFilesWithProgress()` wraps `XMLHttpRequest.upload.onprogress`; capped at 99 % until the 201 lands | `api-client.ts`, `chat-input.tsx` |
| 3 | **Orphan cleanup on chat switch / unmount** — pending attachments previously survived `/c/1 → /c/2` navigations (an upload addressed to chat A could be sent into chat B, or linger unattached forever); now every uploaded-but-unattached media is `DELETE /media/{id}`-cleaned and in-flight batches are aborted when the chat changes or the page unmounts | `releaseAttachments()` runs on `draftKey` change and on unmount; aborted XHRs never reach the server, so no orphan is created | `chat-input.tsx` |
| 4 | **Failed-upload chips are visible** — the `error` field was set but never rendered, so failed chips looked identical to uploading ones; chips now turn red with "Upload failed — tap ✕ to remove" | chip state renders `attachment.error`; size label shows for healthy chips | `chat-input.tsx` |
| 5 | **Friendly upload-error toasts** — 413 / 415 / 503 / 429 / network envelopes map to clear, actionable messages (size limit, allowed formats, storage down, Retry-After countdown) | `mediaUploadErrorMessage()` status switch on `ApiError` | `chat-input.tsx` |
| 6 | **Per-mode presigned-URL inflight dedup** — the URL cache previously keyed in-flight promises by media id only, so a `forceBlob` caller could receive a presigned promise (possibly unreachable); keys are now `presigned:<id>` / `blob:<id>` | `media-url-cache.ts` inflight keying | `media-url-cache.ts` |
| 7 | **Escape-closable image lightbox** — the full-screen preview now dismisses with Escape (and auto-focuses Close) instead of trapping keyboard users | `ImageLightbox` keydown listener | `message-item.tsx` |
| 8 | **Release hygiene** — `APP_VERSION` was still 1.7.0 after two releases; bumped to 1.9.0 together with `package.json` + README | — | `constants.ts`, `package.json`, `README.md` |

## Verified as already correct (Category-2 checklist)

- **Upload guards** — 1–4 files per request, 10 MiB per file, allow-list
  (JPEG/PNG/GIF/WebP/PDF/TXT/CSV), empty-file rejection, multipart field
  repeated `files`, picker `accept=` filter, per-request 60 s timeout
  (`MEDIA_UPLOAD_TIMEOUT_MS`).
- **Metadata dialog** — `GET /media/{id}` fetches fresh metadata (name, type,
  size, dimensions, uploader, status badge, message id, SHA-256) for both
  images and document cards.
- **Presigned URL cache** — in-memory zustand cache with `refreshAt =
  now + expires_in_seconds − 30 s`, re-request only near expiry, inflight
  dedup, `409/501` → authenticated blob fallback, `<img>` `onError` heals via
  `reportError()`.
- **Authenticated content** — `GET /media/{id}/content` Bearer-streams bytes
  for the local-storage fallback and powers the document Download button with
  the real filename (`saveBlob`), retrying through the blob route when the
  presigned link is dead.
- **Delete-on-remove** — chip ✕ on an uploaded-but-unattached attachment calls
  `DELETE /media/{id}` (attached media answers 409 and is ignored).
- **Attach flow** — paperclip on my own text-only messages with a server id:
  upload + `POST /media/{id}/attach { message_id }`, optimistic local update,
  reconciled for everyone via the WS `message_updated` frame.

---

# Backend Endpoint Audit — v1.7.0 (historical)

Audit of `upload/openapi.json` + `upload/README.md` against the frontend call
sites (`src/**`) after the v1.7.0 changes.

**Result: full REST + pagination coverage retained; v1.7.0 is a UX-polish
release.** All 59 operations of the OpenAPI surface keep at least one call
site. v1.6.0 closed every pagination/race-guard gap; v1.7.0 fixed the visual
review findings (media bubble width, BiDi, composer), integrated the local
custom fonts, and shipped three high-impact roadmap features (per-chat
drafts, the Ctrl+K command palette, and the unread-badged scroll-to-bottom
FAB).

## Delivered in v1.7.0

| # | Change | Mechanics | Files |
|---|--------|-----------|-------|
| 1 | **Media bubble width fix** — outgoing image bubbles no longer stretch across the screen with a huge empty colored block | bubble column is `w-fit min-w-0 max-w-[78%] sm:max-w-[65%]` + `overflow-hidden rounded-2xl`; `MediaImage` uses a definite `w-[280px] max-w-full` frame so the intrinsic `width`/`height` attributes of huge photos cannot inflate the shrink-to-fit bubble | `message-item.tsx`, `message-media.tsx` |
| 2 | **Full RTL / BiDi support** — Persian/Arabic messages right-align, English left-aligns, per message | `dir="auto"` + `.bidi-plaintext` (`unicode-bidi: plaintext`) on every message body, sender label, composer textarea and sidebar/command-palette previews | `message-item.tsx`, `chat-input.tsx`, `chat-sidebar.tsx`, `globals.css` |
| 3 | **Composer upgrade** — single-line input replaced by an auto-resizing textarea | grows 1 → 5 rows (`max-h` 120 px), `Enter` sends, `Shift+Enter` inserts a newline; **emoji picker popover** (curated, dependency-free, 3 categories) inserts at the caret next to the attachment clip | `chat-input.tsx`, `emoji-picker.tsx` |
| 4 | **Panel & bubble polish** — the right info panel slides in smoothly instead of squashing the feed; consecutive same-sender bubbles get tail/seam corners | info sidebar animates `width` (`w-0 → w-80/lg:w-96`, `duration-300 ease-in-out`, inner pane fixed-width so content never reflows); `rounded-2xl` bubbles with `bubble-tail-out/in` + `bubble-seam-top/bottom` grouping | `info-sidebar.tsx`, `message-item.tsx`, `globals.css` |
| 5 | **Local custom font integration** — `public/fonts/` loaded through `next/font/local` | Vazirmatn variable (100–900, Latin + Persian/Arabic) as `--font-vazirmatn` → Tailwind `font-sans` on `<body>`; Rajdhani 300–700 → `--font-display`; Cinzel Decorative 400/700/900 → `--font-brand`; all with `display: "swap"` | `layout.tsx`, `globals.css` |
| 6 | **Per-chat draft persistence** (roadmap #8) | debounced localStorage writes keyed by `${chatType}:${conversationId}`, restored on chat switch, cleared on send and on logout/forced-logout; module-level cache keeps restores synchronous | `draft-store.ts`, `chat-input.tsx`, `use-auth-store.ts` |
| 7 | **Command palette / global search** (roadmap #3) | global `Ctrl/Cmd+K` opens a shadcn `CommandDialog`: instant local search over chats/groups/contacts, debounced (300 ms) race-guarded `GET /groups/search` section, plus actions (new chat/group, discover, invitations, contacts, settings, theme toggle) | `command-palette-dialog.tsx`, `ui/command.tsx`, `use-ui-store` |
| 8 | **Scroll-to-bottom FAB & jump-to-unread** (roadmap upgrade of the old jump button) | appears past 300 px from the newest message, carries the server `unread_count` badge (99+ cap), smooth-scrolls to the newest message, advances the WS read watermark and reconciles the counter locally | `message-feed.tsx` |

## Newly integrated in v1.6.0

| # | Upgrade | Endpoints & mechanics |
|---|---------|----------------------|
| 1 | "Load older chats" — sidebar chats list pages with `offset` (`limit = 50`, dedupe by id, "Showing X of Y" footer, refreshes preserve already-loaded older rows) | `GET /api/v1/conversations?limit&offset` |
| 2 | Same paging for the Groups tab | `GET /api/v1/groups?limit&offset` |
| 3 | Group members auto-pagination — page 1 (100 rows) renders instantly, remaining pages load in the background with a progress row ("Loading members… 137/250"), capped at 2 000 rows | `GET /api/v1/groups/{id}/members?limit&offset` |
| 4 | Invitations center History tab — full record with status filter chips (All / accepted / declined / revoked), respond timestamps, offset paging, accept/decline still offered for pending rows, "Open" shortcut for accepted ones | `GET /api/v1/groups/invitations?status&limit&offset` |
| 5 | Group panel invitation history — same status chips + paging inside "Group details"; revoke button only on pending rows; header count reflects the filtered total | `GET /api/v1/groups/{id}/invitations?status&limit&offset` |
| 6 | Search race guard — new `useRaceGuard` hook gives every request a sequence ticket; a slow response for an old query is dropped instead of overwriting fresh results (applies to new-chat search, group discovery, and the group-panel invite search) | `GET /api/v1/users/search` |
| 7 | Group discovery paging — "Load more (n/total)" appends `offset` pages after the first 20 results; race-guarded like #6 | `GET /api/v1/groups/search?q&limit&offset` |
| 8 | Document cards no longer die with the presigned URL — explicit download button fetches the bytes, verifies reachability, and on failure transparently re-resolves through the authenticated blob route (cache-consistent, `reportError()` heals the card) with a failure notice on total loss | `GET /api/v1/media/{id}/content` (+ `/media/{id}/url`) |
| 9 | Contacts & blocklist paging — "Load more contacts (n/total)" on the address book and the same for Settings → Privacy → Blocked accounts; add/remove keep `total` in sync | `GET /api/v1/contacts?limit&offset`, `GET /api/v1/blocks?limit&offset` |
| 10 | `ready` catch-up for busy accounts — after every (re)connect the socket now (a) probes up to 12 unknown ids via the detail endpoints (v1.5.0 behavior) and (b) auto-pulls up to 4 extra pages of BOTH lists until every conversation id from the ready frame is known (`READY_CATCHUP_MAX_PAGES`, bounded burst, boot-safe) | WS `ready` + `GET /conversations` + `GET /groups` (offset pages) |

## Endpoint coverage — complete inventory (v1.6.0, unchanged in v1.7.0)

- **meta:** `GET /` (About panel), `GET /health`, `GET /health/ready`.
- **Auth:** register, login, `GET /auth/me` (boot probe), logout, logout/all,
  password change/reset (request+confirm), email verify request+confirm,
  sessions list/revoke.
- **Users:** `GET/PATCH /users/me`, `PATCH /users/me/privacy`,
  `GET /users/search` (race-guarded), `GET /users/{username}`.
- **Contacts:** list (paged) / add / remove. **Blocks:** list (paged) /
  create / delete.
- **Conversations:** list (paged + "load older"), detail (deep-link resolve),
  create, message history (+reverse paging), send (WS + REST fallback),
  media upload, HTTP read fallback.
- **Groups:** list (paged + "load older"), detail (deep-link resolve), create,
  patch, members (auto-paginated, roles/remove), search (race-guarded +
  paged) + join, invitations (global history, per-group history, create,
  accept/decline, revoke POST + DELETE fallback), transfer-owner, leave,
  group message history/send, HTTP read fallback.
- **Media:** upload (`files` multipart), metadata (`GET /media/{id}`),
  presigned `/url` with authenticated `/content` blob fallback for images
  **and document cards**, delete, attach-to-message.
- **WebSocket:** auth handshake, `ready` bootstrap (identity + id-list
  reconcile **+ bounded list catch-up**), ping/pong, message/ack,
  `message_updated`, typing, read + delivered watermarks, group lifecycle
  events, invitation events, resilient reconnect, HTTP read fallback.

## Next 10 most valuable upgrades

There are **no unimplemented endpoints left**; roadmap items #3 (command
palette) and #8 (drafts) were delivered in v1.7.0, so the list was rebuilt
from the remaining deeper integrations and product completions, prioritized:

| # | Area | Gap & suggested upgrade |
|---|------|--------------------------|
| 1 | `GET …/messages` (both scopes) | **Jump-to-first-unread**: when `unread_count > 0`, compute the opening `offset` from `total − unread` so chats with more unread than one page open at the unread separator instead of always the newest 50. Pairs with the v1.7.0 FAB badge (long-press → jump to first unread). |
| 2 | Sidebar badges | **Unread truthfulness for truncated lists**: tab badges sum loaded rows only; with > 50 chats they undercount. Render bounded badges ("48+") using list `total` and the ready-frame id count until all pages are loaded. |
| 3 | `GET /media/{id}/url` | **Copy media link** action on attachments: fetch a fresh presigned URL on demand for sharing; pairs with a context menu on images and document cards. |
| 4 | `POST /groups` + `POST …/invitations` | **Invite during group creation**: batch-invite the first members in the create dialog instead of one-by-one from the panel afterwards. |
| 5 | Outbox resilience | **Auto-retry failed sends on reconnect**: replay `status = "failed"` messages (same idempotent `client_message_id`) when the socket reopens or the REST fallback returns. |
| 6 | Offline / boot speed | **IndexedDB feed cache**: persist recent feeds + lists for instant paint and offline reading; reconcile against the ready frame on reconnect (PWA service worker for shell caching). |
| 7 | Media listing | **Per-chat media gallery** ("Media & files" tab in the info panel) — requires a backend `GET /conversations/{id}/media` listing endpoint (only the POST upload exists today); flag to the backend team. |
| 8 | A11y / keyboard | Roving focus in chat lists, `aria-live` announcements for incoming messages, focus-trap verification for all dialogs/sheets; full Persian UI translation alongside the BiDi text pipeline. |
| 9 | Composer UX | Draft niceties: draft age indicator ("drafted 2 h ago"), multi-line draft preview in the sidebar, and a swipe-to-clear gesture on the chip. |
| 10 | Palette depth | Command palette actions with arguments: "message @user" via `GET /users/search`, "create group named …", recent-emoji memory and fuzzy scoring tuned for Persian queries. |

## Known product gaps that are NOT endpoint gaps

- Avatars — `avatar_key` is rendered as initials only; the backend ships no
  avatar upload/serving endpoint yet (README: "infrastructure only").
- E2EE — not in the backend contract at all (roadmap).
- Message edit/delete — the backend exposes neither; `message_updated` only
  reflects media-attach and status changes.
