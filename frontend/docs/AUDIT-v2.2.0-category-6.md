# Category 6 Audit Report (FINAL) — Direct Conversations, Messaging & Realtime Delivery

**Release:** messenger-frontend-v2.2.0 (Complete API Coverage milestone)
**Baseline:** v2.1.0 (Category 4 & 5 — Users, Profiles, Privacy, Contacts & Blocks)
**Contract sources:** `upload/openapi.json` + `upload/README.md` (FastAPI Messenger backend; the WebSocket section of the README is the frame contract)
**Verification:** `tsc --noEmit` PASS · `eslint .` PASS (0 issues) · `next build` PASS (10 routes) · E2E browser run PASS (unread divider, REST-fallback send + ticks, sidebar preview sync)

---

## Endpoint Audit Table (6 direct-chat HTTP endpoints + WS parity)

| # | Method | Path / Frame | Implementation file(s) | UI trigger | Status |
|---|--------|--------------|------------------------|------------|--------|
| 1 | POST | `/api/v1/conversations` | `src/stores/use-conversation-store.ts` (`openDirectChat`) | New Chat dialog (`start`), Contacts page "Message" button, profile drawer "Message" — all navigate to `/c/{id}`; 400 self / 403 blocked / 404 unknown surface as friendly `ApiError` toasts | `[Verified Existing]` (idempotency transparent: `apiRequest` treats 200 and 201 as success, so "existing" returns resolve identically to "created") |
| 2 | GET | `/api/v1/conversations` | `src/stores/use-conversation-store.ts` (`loadConversations`, `loadOlderConversations`); `src/components/layout/chat-sidebar.tsx` | Sidebar "Chats" tab: sorted by `last_message_at` (fallback `created_at`), realtime preview + timestamp via WS `ack`/`message`, unread badge, "Load older chats" row when `total > items.length`; reconnect force-refresh + `ready`-frame reconcile/catch-up | `[Verified Existing]` |
| 3 | GET | `/api/v1/conversations/{conversation_id}` | `src/stores/use-conversation-store.ts` (`resolveDirectChat` + inflight dedupe + dead-probe cache); `src/components/chat/chat-viewport.tsx` | Deep-link self-healing: refreshing or opening `/c/{id}` when the chat is absent from the 50-row lists probes the detail endpoint once to bootstrap header (participant `UserPublic`, last seen), then renders; 404 → "Conversation unavailable" state | `[Verified Existing]` |
| 4 | POST | `/api/v1/conversations/{conversation_id}/messages` | `src/stores/use-message-store.ts` (`sendMessage`, `retryMessage`); `src/components/chat/chat-input.tsx` | REST fallback when the WS is down (optimistic pending bubble → server id via `confirmAck`, failure → `failed` status with retry), primary path for media-only sends; `body` 1–4000 trimmed + `media_id` validated client-side | `[Verified Existing]` (E2E-verified: send with socket offline → pending → sent tick → sidebar preview bump) |
| 5 | GET | `/api/v1/conversations/{conversation_id}/messages` | `src/stores/use-message-store.ts` (`loadFeed` newest-window probe + `loadOlder` reverse paging); `src/components/chat/message-feed.tsx` | Chronological oldest-first feed; scrolling to the top (or the "Load older messages" button) pages backwards via `limit`/`offset` **without scroll jumps** (`prevScrollHeightRef` delta anchoring); day separators; `dir="auto"` BiDi text | `[Verified Existing]` |
| 6 | POST | `/api/v1/conversations/{conversation_id}/read` | `src/stores/use-socket-store.ts` (`postReadRest` fallback + `sendRead` throttle); `src/components/chat/message-feed.tsx` (auto-read at bottom / on focus / on jump) | Auto-invoked when the conversation is mounted pinned to latest, when new incoming messages arrive while visible, on window focus, and on jump-to-latest; `{ last_message_id }` watermark; receipt `unread_count` syncs the sidebar badge; companion WS `{ type: "read" }` frame with throttle + REST fallback when the socket is down | `[Verified Existing]` |
| WS | → | `message` frame | `use-socket-store.ts` (`handleServerFrame` case `message`); `use-message-store.ts` (`applyIncomingMessage` with optimistic-duplicate replacement); `use-conversation-store.ts` (`applyIncomingMessage` preview + unread) | In-view: appended to the active feed + auto-read watermark; out-of-view: sidebar preview + unread counter + desktop notification | `[Verified Existing]` |
| WS | → | `ack` frame | `use-message-store.ts` (`confirmAck`); `use-socket-store.ts` | Matches `client_message_id`, clears pending (clock), reconciles server `id` + `delivered_at`; duplicate replays reconcile in place | `[Verified Existing]` |
| WS | → | `delivered` watermark | `use-message-store.ts` (`applyDeliveredWatermark`) | Advances ticks for **all** my messages with `id <= message_id` in one pass (single ✓ → ✓✓ gray) | `[Verified Existing]` |
| WS | → | `read` watermark | `use-message-store.ts` (`applyReadWatermark`); `use-socket-store.ts` (own-echo → `markReadLocally`) | Advances ticks for all my messages up to `message_id` (✓✓ gray → ✓✓ sky); my own read echo clears the sidebar unread badge; group variant tracks per-reader watermarks | `[Verified Existing]` |
| WS | → | `typing` frame | `use-message-store.ts` (`setTyping`, `pruneTyping`); `src/hooks/use-typing.ts` (1 s prune ticker); `typing-indicator.tsx`; header subtitle "typing…" | Animated indicator under the feed + header; auto-expiry after **5 s** (`TYPING_EXPIRY_MS = 5000`) even when the stop frame is lost; stop frame clears instantly | `[Verified Existing]` |
| WS | → | `message_updated` frame | `use-message-store.ts` (`applyMessageUpdated`) | Media attach echo replaces the message with the same `id` instead of appending | `[Verified Existing]` |
| WS | ← | `message` / `read` / `typing` / `ping` / `pong` client frames | `use-socket-store.ts` (`transportMessage`, `sendRead`, `sendTyping`, heartbeat pong) | Send pipeline, read watermarks, typing start/stop (stop never throttled), ping/pong keepalive | `[Verified Existing]` |

---

## Messaging Mechanics Verification (task checklist)

| # | Mechanic | Where | Status |
|---|----------|-------|--------|
| 1 | Conversation creation idempotency (201 created vs 200 existing) | `openDirectChat` + `apiRequest` (both 2xx accepted; server guarantees no duplicate row) | ✅ `[Verified Existing]` |
| 2 | Deep-link self-healing for `/c/[id]` via `GET /conversations/{id}` | `resolveDirectChat` (inflight dedupe + 5 min dead-probe cache) + `ChatViewport` resolution effect | ✅ `[Verified Existing]` |
| 3 | Reverse infinite scrolling without scroll jumps | `loadOlder` (`offset` backwards) + `prevScrollHeightRef` scroll anchoring + "Load older messages" affordance | ✅ `[Verified Existing]` |
| 4 | Status ticks ⏱ pending → ✓ sent → ✓✓ delivered (gray) → ✓✓ read (sky) | `status-tick.tsx` (Clock / Check / CheckCheck muted / CheckCheck sky-500) driven by `id` / `delivered_at` / `read_at` | ✅ `[Verified Existing]` |
| 5 | Read watermark sync via HTTP `POST /read` **and** WS `{ type: "read" }` | `sendRead` (throttled, monotonic per conversation) + `postReadRest` on socket failure; auto triggers at bottom / focus / jump | ✅ `[Verified Existing]` |
| 6 | Ephemeral typing indicator with 5 s fallback expiry | `setTyping` stores expiry epoch (`TYPING_EXPIRY_MS = 5000`); `useTypingUsers` prune ticker + render-time filter; stop frame clears | ✅ `[Verified Existing]` |
| 7 | Jump to first unread / "Unread Messages" divider when `unread_count > 0` | `message-feed.tsx`: `findFirstUnreadId` walks the loaded window backwards counting incoming messages (read watermark is monotonic ⇒ unread incoming form a suffix); divider pill renders before the anchored message (`data-unread-divider`), viewport opens there (`UNREAD_TOP_OFFSET`) instead of bottom; cleared on jump-to-latest / chat switch | ✅ `[Newly Implemented]` |
| 8 | Scroll-anchoring validation for jumpy layouts | Prepend anchoring effect (delta `scrollTop += delta`), divider jump uses `offsetTop`, cached-feed switch pins to newest | ✅ `[Newly Implemented]` (switch-to-cached-feed bottom pin added alongside the divider) |
| 9 | Blocked-pair guards (send/typing refused, composer gated) | `chat-input.tsx` composer gate + inline Unblock notice; server 403 → `error` frame → `markFailed` + toast | ✅ `[Verified Existing]` |
| 10 | `dir="auto"` BiDi text formatting | `message-item.tsx` (bubble body + preview text) | ✅ `[Verified Existing]` |

---

## Newly Implemented in v2.2.0

### 1. "Unread messages" divider + jump-to-first-unread (`src/components/chat/message-feed.tsx`)
- **Boundary math:** `findFirstUnreadId()` counts `unread_count` incoming messages backwards from the newest loaded message. Because the backend read watermark is monotonic (marking `id <= last_message_id`), unread incoming messages always form a suffix of the incoming sequence, so the walk lands exactly on the server's boundary. When the unread block extends beyond the loaded window, the fallback keeps the previous bottom-pinned behavior.
- **Divider UI:** sky-accent pill (`Unread messages`) with flanking rules, rendered inside the anchored message wrapper (`data-mid` attribute added to every row; `role="separator"`, `aria-label="Unread messages"`). It is a reading aid: captured once per conversation entry and cleared on jump-to-latest or chat switch, mirroring mainstream messenger behavior.
- **Viewport opening:** the first-load effect defers to the anchor (`skipBottomScrollRef`) and scrolls the anchored message to the top edge (`offsetTop − 84px`), so the user reads forward from the boundary instead of landing at the newest bubble.
- **Cached-feed switch:** switching between already-loaded chats now pins to the newest message (previously the scroll position leaked across chats) — unless the target conversation still has unread entries, in which case the divider opens it at the boundary.

### 2. Packaging / metadata
- `package.json` → `2.2.0`, `APP_VERSION` constant → `2.2.0` (Settings → About), README header + feature map updated.

---

## Contract Guard Verification

### Frame hygiene (README "Realtime chat (WebSocket)")
- The `auth` frame is the **first** frame after `onopen` and within 5 s (`WS_AUTH_TIMEOUT_MS`); close codes 4401/4403 purge the session and redirect to `/login`, 4429 surfaces "Too many connections".
- Server `ping` is answered with `pong` immediately; the client additionally pings every 25 s and force-closes a >70 s silent link.
- `client_message_id` (UUID v4, 1–64 chars) is repeated verbatim on retries with the **same** body **and** `media_id` — matching the backend's immutable-key rule; `duplicate: true` replays reconcile in place without re-appending.
- Read frames are throttled above the server's `WS_READ_MIN_INTERVAL_SECONDS` (client 1 s vs server 0.5 s) and never repeat an older watermark (`lastReadSentId` monotonic per conversation). Typing start frames are throttled at 1.5 s; stop frames bypass the throttle per contract.
- Watermark events are applied as single passes over `id <= message_id` — never per-row tracking.
- `error` frames with a `client_message_id` flip the matching pending bubble to `failed` (retry affordance) and toast the server message; the socket is never killed by a bad frame (handler try/catch).

### HTTP/WS parity
- Every mutating action has a REST fallback: sends (`POST …/messages`), reads (`POST …/read`). The sidebar and unread counters resync from the authoritative list on every reconnect (`ready` → force refresh + reconcile + bounded catch-up), so missed frames self-heal.

---

## Verification Log

| Gate | Result |
|------|--------|
| `npx tsc --noEmit` | PASS (0 errors) |
| `npm run lint` | PASS (0 issues) |
| `npm run build` | PASS (10 routes: `/`, `/c/[id]`, `/g/[id]`, `/contacts`, `/settings`, auth pages) |
| E2E: divider renders above first unread incoming message (`data-mid="12"`) | PASS |
| E2E: REST-fallback send → pending → sent tick → realtime sidebar preview | PASS |
| E2E: status ticks + day dividers + header presence/typing subtitle | PASS |

**Result: Categories 1–6 now cover the full Messenger API surface — Complete API Coverage milestone reached.**
