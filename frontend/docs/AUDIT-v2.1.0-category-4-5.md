# Category 4 & 5 Audit Report — Users, Profiles, Privacy, Contacts & Blocks

**Release:** messenger-frontend-v2.1.0
**Baseline:** v2.0.0 (Category 3 — Authentication, Account Security & Device Sessions)
**Contract sources:** `upload/openapi.json` + `upload/README.md` (FastAPI Messenger backend)
**Verification:** `tsc --noEmit` PASS · `eslint .` PASS (0 issues) · `next build` PASS (10 routes)

---

## Endpoint Audit Table (11 endpoints)

| # | Method | Path | Implementation file(s) | UI trigger | Status |
|---|--------|------|------------------------|------------|--------|
| 1 | GET | `/api/v1/users/me` | `src/stores/use-auth-store.ts` (`login`, `refreshProfile`, `probeSession`) | App bootstrap / session restore; Settings → Profile tab prefill (`ProfilePanel`) | `[Verified Existing]` |
| 2 | PATCH | `/api/v1/users/me` | `src/app/(main)/settings/page.tsx` (`ProfilePanel.onSubmit`) | Settings → Profile tab → "Save profile" | `[Newly Implemented]` (409 inline form error + optimistic store update + change-only username send) |
| 3 | PATCH | `/api/v1/users/me/privacy` | `src/app/(main)/settings/page.tsx` (`PrivacyPanel.save`) | Settings → Privacy tab: visibility radio group + "Show last seen" switch + "Save privacy" | `[Verified Existing]` |
| 4 | GET | `/api/v1/users/search` | `src/components/dialogs/new-chat-dialog.tsx`, `src/components/dialogs/create-group-dialog.tsx`, `src/components/layout/info-sidebar.tsx` (`GroupInfo`) | New Chat dialog search; Create-group invite search; Group panel invite search | `[Newly Implemented]` ("Load more" offset paging in all three; `useRaceGuard` added to create-group search) |
| 5 | GET | `/api/v1/users/{username}` | `src/components/layout/info-sidebar.tsx` (`UserProfilePanel`) | Click a group member / "View profile" in chat header menu → profile drawer (bio, avatar, joined date, relative last-seen, 404 → "Profile unavailable") | `[Verified Existing]` |
| 6 | POST | `/api/v1/contacts` | `src/stores/use-contacts-store.ts` (`add`); `src/app/(main)/contacts/page.tsx`; `info-sidebar.tsx` (`DirectInfo`, `UserProfilePanel`); `chat-viewport.tsx` (`DirectHeaderMenu`) | Contacts page "Add by username" form; "Add to contacts" in profile drawer & header menu | `[Verified Existing]` (self-add 400 / blocked 403 / 404 / duplicate 409 surface as friendly ApiError toasts) |
| 7 | GET | `/api/v1/contacts` | `src/stores/use-contacts-store.ts` (`load`, `loadMore`); `src/app/(main)/contacts/page.tsx` | Contacts page paginated list (avatar, display name, @username, Start-chat + Remove actions, "Load more contacts") | `[Verified Existing]` |
| 8 | DELETE | `/api/v1/contacts/{username}` | `src/stores/use-contacts-store.ts` (`remove`); `contacts/page.tsx` (confirm dialog); `info-sidebar.tsx` (`DirectInfo`, `UserProfilePanel` confirm dialogs) | Remove icon in Contacts list; "Remove from contacts" in profile drawer | `[Newly Implemented]` (confirmation `AlertDialog` in Contacts page + both profile drawers; optimistic store update was already present) |
| 9 | POST | `/api/v1/blocks` | `src/stores/use-blocks-store.ts` (NEW); `info-sidebar.tsx` (`DirectInfo`, `UserProfilePanel`); `chat-viewport.tsx` (`DirectHeaderMenu`); `chat-input.tsx` (composer gate) | "Block user" in profile drawers; chat header ⋮ context menu → "Block user…" | `[Newly Implemented]` (destructive `AlertDialog` guard; kebab context menu in direct-chat header; blocked-state sync: contact status hidden, "Blocked" badge, composer disabled with Unblock action) |
| 10 | GET | `/api/v1/blocks` | `src/stores/use-blocks-store.ts` (`load`, `loadMore`); `settings/page.tsx` (`BlockedAccountsSection`) | Settings → Privacy → "Blocked accounts" list (avatar, display name, @username, paginated "Load more") | `[Verified Existing]` (refactored onto the shared store for cross-UI sync) |
| 11 | DELETE | `/api/v1/blocks/{username}` | `src/stores/use-blocks-store.ts` (`unblock`); `settings/page.tsx`; `info-sidebar.tsx`; `chat-viewport.tsx`; `chat-input.tsx` | "Unblock" button in Blocked accounts; "Unblock user" in profile drawers & header menu; "Unblock" in composer notice | `[Verified Existing]` (now reachable from 5 surfaces; re-enables chat interaction immediately) |

---

## Contract Guard Verification

### PATCH /users/me — `extra="forbid"`
- Payload restricted to `username?`, `display_name?`, `bio?`, `profile_visibility?`, `show_last_seen?` — `id`, `email`, `is_active`, `token_version`, `email_verified`, `avatar_key` are never sent (`ProfileUpdate` typed payload).
- Empty display name / bio are sent as `null`, which clears the field server-side.
- Username client policy enforced by `profileSchema` → `USERNAME_REGEX` (`/^[a-z0-9][a-z0-9_]{2,31}$/` = 3–32 chars, lower-case letters/digits/underscores, starts with letter or digit).
- **409 Conflict** → `form.setError("username", …)` renders "That username is already taken. Try another one." inline under the field (v2.1.0). The optimistic store snapshot is rolled back before the error is surfaced.
- Username is only included in the payload when it actually differs from the current handle, avoiding needless conflict checks on every save.

### PATCH /users/me/privacy
- Radio group for `profile_visibility` (`public` / `contacts` / `private`) + `Switch` for `show_last_seen`; explicit "Save privacy" with toast; response `UserPrivate` written back to `useAuthStore` so badges stay in sync.

### GET /users/search
- `SEARCH_MIN_QUERY_LENGTH = 2` enforced client-side in all three search inputs (query shorter than 2 chars never reaches the API).
- 250 ms debounce + `useRaceGuard` last-response-wins ticketing (stale responses cannot overwrite fresh results).
- "Load more (shown/total)" appears whenever `nextOffset < total` from the `UserPage` metadata (v2.1.0).

### GET /users/{username}
- 404 (deactivated / private profile / blocked pair) renders the "Profile unavailable — this profile is private, blocked, or does not exist" state, never a 403-style leak.
- Relative last-seen rendered only when the API returns `last_seen_at` (owner-controlled visibility); "Last seen hidden" otherwise.

### Contacts & blocks semantics
- `POST /contacts` error mapping: 400 self-add, 403 blocked pair, 404 unknown user, 409 already-a-contact — all surfaced through the uniform `ApiError` envelope as friendly toasts.
- Blocks are applied symmetrically by the backend; the frontend mirrors this by keying all block-state UI (badges, menus, composer gate) off the shared `use-blocks-store` list.
- `POST /blocks` 409 (already blocked, e.g. a parallel device) converges local state instead of erroring.

---

## Newly Implemented (v2.1.0) — File Manifest

| File | Change |
|------|--------|
| `src/stores/use-blocks-store.ts` | **NEW** — blocklist store: `load`/`loadMore` offset paging, `block`/`unblock` with `busyUsername`, 409-converge, registered in the logout reset registry |
| `src/components/layout/info-sidebar.tsx` | Block + remove-contact `AlertDialog` guards (DirectInfo & UserProfilePanel); destructive "Blocked" badge; contact status hidden while blocked; unblock paths; invite-search "Load more" paging |
| `src/components/chat/chat-viewport.tsx` | **NEW** `DirectHeaderMenu` — direct-chat header ⋮ context menu: View profile / Add-Remove contacts / Block user (guarded) / Unblock user |
| `src/components/chat/chat-input.tsx` | Composer replaced by a blocked notice + inline Unblock when the direct participant is blocked; `addFiles`/`send` guarded against blocked pairs |
| `src/components/dialogs/new-chat-dialog.tsx` | Prefix-search "Load more" pagination with `UserPage` metadata |
| `src/components/dialogs/create-group-dialog.tsx` | `useRaceGuard` (stale-response fix) + "Load more" pagination; member-filter moved to a ref to avoid refetch loops |
| `src/app/(main)/contacts/page.tsx` | Remove-contact confirmation `AlertDialog` |
| `src/app/(main)/settings/page.tsx` | Profile tab: optimistic store update, 409 inline username error, change-only username payload; Blocked accounts section refactored onto the shared blocks store |
| `src/lib/constants.ts` | `INVITE_SEARCH_PAGE_LIMIT = 10`; `APP_VERSION` → 2.1.0 |
| `package.json` | version → 2.1.0 |

## Verification Log

| Command | Result |
|---------|--------|
| `npx tsc --noEmit` | PASS (0 errors) |
| `npm run lint` (eslint .) | PASS (0 errors, 0 warnings) |
| `npm run build` (next build) | PASS — 10 routes compiled, static generation OK |
