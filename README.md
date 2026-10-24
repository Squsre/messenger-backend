# Messenger — Backend API

Backend for the messenger project, built with **Python 3.11**, **FastAPI** and
**SQLAlchemy 2.0** (SQLite by default), with **Alembic** for migrations and
**Argon2id + JWT** authentication.

> Delivered so far:
> **Stage 1** — clean FastAPI skeleton with a health endpoint, configuration and tests.
> **Stage 2** — database infrastructure (engine, sessions, base models) and Alembic migrations.
> **Stage 3** — production-ready email/password authentication (register, login, `/me`, logout).
> **Stage 4–6** — user profiles, account security (email verification, password change/reset,
> device sessions) and contacts/search/blocks.
> **Stage 7** — one-to-one private conversations and text messages over HTTP.
> **Stage 8** — realtime delivery over an authenticated WebSocket (in-process; a
> shared pub/sub layer is still deferred).
> **Stage 9** — delivery/read status, unread counters, ephemeral typing
> indicators and WebSocket-driven last seen.
> **Stage 10** — authenticated photo/file upload, validated storage, controlled
> download, deletion of unattached uploads, and media attachments on messages.
>
> Groups and E2EE are still to come. Two-factor authentication and contact
> *requests* are deliberately deferred and documented in the roadmap. Files are
> stored locally behind a `StorageService` interface; MinIO/S3 is a later
> backend, not a requirement for this stage.

## Project layout

```
messenger/
├── app/
│   ├── __init__.py
│   ├── main.py                  # create_app() factory + module-level `app`
│   ├── core/
│   │   ├── config.py            # pydantic-settings configuration
│   │   ├── dependencies.py      # get_app_settings
│   │   ├── errors.py            # AppError hierarchy + exception handlers
│   │   ├── rate_limit.py        # in-process sliding-window limiter
│   │   └── security.py          # Argon2id, JWT, username/avatar policy, opaque tokens
│   ├── db/
│   │   ├── base.py              # declarative Base, naming convention, mixins
│   │   ├── session.py           # engine, Database, get_db dependency
│   │   └── models/
│   │       ├── health_check.py  # infrastructure model (connectivity checks)
│   │       ├── user.py          # accounts: credentials, profile, security state
│   │       ├── token_blocklist.py   # revoked JWT ids (single-token revocation)
│   │       ├── user_session.py  # logged-in devices (id == access-token `jti`)
│   │       ├── verification_token.py # hashed single-use email/reset tokens
│   │       ├── contact.py       # one-directional contact relationships
│   │       ├── block.py         # user-to-user blocks
│   │       ├── conversation.py  # one-to-one conversations (unique pair_key)
│   │       ├── conversation_member.py # participants
│   │       ├── message.py       # append-only messages + delivery/read status
│   │       └── media.py         # conversation-scoped upload metadata (no binaries)
│   ├── services/
│   │   ├── user.py              # registration, lookup, username derivation
│   │   ├── profile.py           # profile updates, visibility, last-seen heartbeat
│   │   ├── token.py             # revocation denylist helpers
│   │   ├── session.py           # device sessions, revoke one / all
│   │   ├── verification.py      # issue + consume email/reset tokens
│   │   ├── email.py             # EmailService: smtp / console / disabled
│   │   ├── contact.py           # contacts, blocks, user search queries
│   │   ├── auth.py              # shared token -> user resolution (HTTP + WebSocket)
│   │   ├── realtime.py          # in-process WebSocket connection registry
│   │   ├── chat.py              # conversations, membership, messages, read watermarks
│   │   ├── storage.py           # StorageService interface + local-disk backend
│   │   ├── media_validation.py  # filename/content sniffing and dimension parsing
│   │   └── media.py             # media rows, authorization, upload/attach rules
│   ├── api/
│   │   ├── deps.py              # token chain, per-scope rate limits, services
│   │   └── v1/
│   │       ├── router.py        # aggregates all v1 routers
│   │       └── endpoints/
│   │           ├── health.py    # GET  /api/v1/health
│   │           ├── auth.py      # /api/v1/auth/{register,login,me,logout}
│   │           ├── account.py   # /api/v1/auth/{email,password,sessions,logout-all}
│   │           ├── users.py     # /api/v1/users/{me,search,{username}}
│   │           ├── contacts.py  # /api/v1/contacts
│   │           ├── blocks.py    # /api/v1/blocks
│   │           ├── conversations.py # /api/v1/conversations
│   │           ├── media.py    # /api/v1/media + conversation uploads
│   │           └── realtime.py  # WS   /api/v1/ws
│   └── schemas/
│       ├── health.py            # response models
│       ├── auth.py              # auth request/response models
│       ├── user.py              # public vs private profile models
│       ├── account.py           # verification, password and session models
│       ├── contact.py           # username reference model
│       ├── chat.py              # conversation/message request + response models
│       ├── media.py             # media metadata + attach request models
│       └── realtime.py          # WebSocket frame schemas + parser
├── migrations/                  # Alembic environment
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_create_health_checks.py
│       ├── 0002_create_users_and_token_blocklist.py
│       ├── 0003_add_profiles_and_social.py
│       ├── 0004_add_conversations_and_messages.py
│       ├── 0005_add_message_client_id.py
│       ├── 0006_add_message_statuses.py
│       └── 0007_add_media.py
├── tests/
│   ├── conftest.py              # fixtures + register/login and email helpers
│   ├── test_app.py              # startup, docs, wiring
│   ├── test_health.py           # health endpoint
│   ├── test_database.py         # engine, sessions, get_db dependency
│   ├── test_migrations.py       # real `alembic upgrade/downgrade` + data backfill
│   ├── test_security.py         # hashing, password policy, JWT
│   ├── test_auth.py             # register / login / me / logout
│   ├── test_rate_limit.py       # login throttling
│   ├── test_profiles.py         # profile reads/edits, protected fields, privacy
│   ├── test_email_verification.py  # token issue/confirm/expiry/reuse + delivery
│   ├── test_password_security.py   # password change and reset
│   ├── test_sessions.py         # device sessions and global invalidation
│   ├── test_search.py           # search ranking, paging, filtering
│   ├── test_contacts.py         # one-directional contacts
│   ├── test_blocks.py           # blocks and their cross-feature effects
│   ├── test_conversations.py    # conversation creation, idempotency, listing, access
│   ├── test_messages.py         # sending, validation, history, paging, blocks
│   ├── test_message_status.py   # delivery/read timestamps, unread counts, last seen
│   ├── _realtime.py             # WebSocket helpers (not collected as a test)
│   ├── test_realtime_auth.py    # handshake: tokens, disabled accounts, timeouts
│   ├── test_realtime_messages.py # send/ack, validation, retries, persistence
│   ├── test_realtime_access.py  # membership, blocks, conversation isolation
│   ├── test_realtime_limits.py  # connection cap, rate limit, heartbeat, manager
│   ├── test_realtime_hardening.py # periodic revalidation, pending caps, retries
│   ├── test_realtime_status.py  # delivered/read events, typing indicators
│   ├── _media.py               # tiny valid image/PDF/CSV upload helpers (not collected)
│   ├── test_media.py           # upload validation, storage, auth, delete, cleanup
│   └── test_media_messages.py  # send/attach media, history, WS, idempotency
├── alembic.ini
├── .env.example
├── .gitignore
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

## Database

SQLite is used by default and lives at `./messenger.db` (configurable through
`DATABASE_URL`). No server needs to be installed.

### Migrations (Alembic)

```powershell
# Apply every migration (create/update the schema)
.\.venv\Scripts\alembic upgrade head

# Roll back the most recent migration
.\.venv\Scripts\alembic downgrade -1

# Roll back everything
.\.venv\Scripts\alembic downgrade base

# Show the current revision
.\.venv\Scripts\alembic current

# Create a new (empty) revision
.\.venv\Scripts\alembic revision -m "add some table"

# Autogenerate a revision from the models
.\.venv\Scripts\alembic revision --autogenerate -m "describe change"
```

The database URL is read from `DATABASE_URL` (via `.env`), so the same commands
work unchanged after switching to PostgreSQL.

Current head: **`0007_add_media`**. Apply it to an existing
database (created by an earlier stage) with a single `alembic upgrade head`:

| Revision | What it adds |
| -------- | ------------ |
| `0001_create_health_checks` | `health_checks` (infrastructure). |
| `0002_create_users_and_token_blocklist` | `users`, `token_blocklist` (auth). |
| `0003_add_profiles_and_social` | Profile/security columns on `users`, plus `user_sessions`, `verification_tokens`, `contacts`, `blocks`. |
| `0004_add_conversations` | `conversations`, `conversation_members`, `messages` (one-to-one chat). |
| `0005_message_client_id` | `messages.client_message_id` plus the unique index that makes realtime retries idempotent. |
| `0006_message_statuses` | `messages.delivered_at` and `messages.read_at` (recipient delivery/read timestamps). |
| `0007_add_media` | `media` (conversation-scoped upload metadata) and a nullable `messages.body` so a message can be media-only. |

`0003` is written to be safe on a database that already contains accounts:
`users.username` is added **nullable**, every existing account is backfilled
with a deterministic handle derived from its email (de-duplicated in `id` order,
reserved names avoided), and only then is the column made `NOT NULL` and given
its unique index. Existing rows, ids and passwords are never rewritten. The
backfill is deterministic, so downgrading and re-upgrading lands on the same
names.

`0004` only **creates new tables**; it alters nothing that already existed, so it
is safe on a populated database.

`0005` adds a **nullable** column and a unique index. Existing messages keep
`client_message_id = NULL` (and are therefore unconstrained), no row is
rewritten, and `downgrade` removes only the key, never the messages.

`0006` adds two **nullable** timestamp columns (`delivered_at`, `read_at`).
Every existing message starts as "sent, not delivered/read"; no row is rewritten
and no constraint or index is touched. `downgrade` removes only those two
columns, never the messages.

`0007` adds the `media` table and makes `messages.body` **nullable** so a
message can be media-only. SQLite cannot change nullability or replace a CHECK
constraint in place, so the migration rebuilds `messages` on SQLite while copying
every row, primary key, foreign key, CHECK constraint and index. `media` is
created after the rebuild, and the upgrade is safe on a populated database.
Downgrading refuses if any `body IS NULL` row exists instead of deleting
messages or inventing placeholder text; add text to those messages first or
restore a backup.

> **Rollback warning:** from `0007`, `alembic downgrade -1` drops `media` and
> restores `messages.body NOT NULL` (refusing if media-only messages exist);
> `-2` additionally removes the status timestamps; `-3` removes the idempotency
> key. `-4` drops the three chat tables *and the messages they hold*; `-5` drops
> the profile, verification and session columns *and their data*. All of these
> are exercised by the test suite, but do not run them on a production database
> that has real data without a backup.


### Session usage in future endpoints

```python
from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db

@router.get("/example")
def example(db: Session = Depends(get_db)) -> dict[str, str]:
    ...
```

`get_db` yields a request-scoped session that commits on success, rolls back
on error and is always closed afterwards.
For background work, use the context manager, which commits on success and
rolls back on error:

```python
from app.db.session import get_database

with get_database().session() as session:
    session.add(SomeModel(...))
```

### Moving to PostgreSQL later

1. `python -m pip install "psycopg[binary]"` (plus driver of choice).
2. Set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/messenger` in `.env`.
3. Run `alembic upgrade head`.

No code changes are required: the engine factory only special-cases SQLite.

## Authentication

Email + password authentication with **Argon2id** password hashing and
short-lived **JWT** access tokens. All routes live under `/api/v1/auth`.

| Method | Path                     | Auth | Description                                  |
| ------ | ------------------------ | ---- | -------------------------------------------- |
| POST   | `/api/v1/auth/register`  | —    | Create an account (`201`, returns the user). |
| POST   | `/api/v1/auth/login`     | —    | Exchange credentials for an access token.    |
| GET    | `/api/v1/auth/me`        | Bearer | Return the authenticated user.             |
| POST   | `/api/v1/auth/logout`    | Bearer | Revoke the presented access token.         |

### Quick start

```powershell
# 1. Register
curl.exe -s -X POST http://127.0.0.1:8000/api/v1/auth/register `
  -H "Content-Type: application/json" `
  -d '{\"email\":\"user@example.com\",\"password\":\"Str0ng-Passw0rd!\"}'

# 2. Log in (copy access_token from the response)
curl.exe -s -X POST http://127.0.0.1:8000/api/v1/auth/login `
  -H "Content-Type: application/json" `
  -d '{\"email\":\"user@example.com\",\"password\":\"Str0ng-Passw0rd!\"}'

# 3. Current user
curl.exe -s http://127.0.0.1:8000/api/v1/auth/me `
  -H "Authorization: Bearer <access_token>"
```

In Swagger (`/docs`) click **Authorize** and paste the raw token — the
`HTTPBearer` scheme sends it as `Authorization: Bearer <token>`.

### Password policy

Enforced on registration and documented here (see `app/core/security.py`):

* length **12–128** characters,
* at least one lowercase letter, one uppercase letter, one digit and one
  special character.

Rejected passwords produce a `422` validation error. Passwords are hashed with
**Argon2id** (64 MiB memory, 3 iterations, 4 lanes) and the raw value is never
stored, logged or returned.

### Tokens

* Algorithm `HS256` (configurable among `HS256`/`HS384`/`HS512`; nothing else
  is accepted, so `alg: none` and algorithm-confusion attacks are impossible).
* Claims: `sub` (user id), `jti` (unique id), `iat`, `nbf`, `exp`, `iss`, `aud`.
  Signature, algorithm, audience, issuer and expiry are all verified.
* Default lifetime: **15 minutes** (`ACCESS_TOKEN_EXPIRE_MINUTES`).
* The signing key is read **only** from `JWT_SECRET_KEY` in the environment and
  has no default — the application refuses to start without it.

### Logout and token revocation

JWTs are stateless: without server-side state a token stays valid until it
expires. This project therefore implements a **database-backed denylist**
(`token_blocklist`):

* `POST /logout` stores the token's `jti` (with its expiry) and returns `200`.
* Every authenticated request checks the denylist, so the token is rejected
  with `401` immediately afterwards — even though it has not expired.
* Expired rows are pruned opportunistically on login.

**Trade-offs (documented honestly):** each authenticated request performs one
extra indexed lookup; the denylist is only as available as the database; and a
shared database is required once the API runs on several hosts. A Redis/`jti`
cache would be the natural next step, but is intentionally out of scope here.

### Login rate limiting

`POST /login` is throttled by an **in-process sliding-window limiter**
(`app/core/rate_limit.py`), keyed by **client IP** *and* **target account**, so
neither one host guessing many accounts nor many hosts guessing one account goes
unnoticed. Limits: 5 attempts / 60 s (`LOGIN_RATE_LIMIT_ATTEMPTS`,
`LOGIN_RATE_LIMIT_WINDOW_SECONDS`). Exceeding a limit returns `429` with a
`Retry-After` header.

**Limitation:** state is per process, so with multiple workers each enforces its
own bucket, and counters reset on restart. A shared store (Redis) is the
follow-up once the service scales horizontally. Set `RATE_LIMIT_ENABLED=false`
to disable it (e.g. behind an external gateway that already throttles).

### Security notes

* Identical `401` for an unknown email and a wrong password; a dummy verify is
  performed for unknown emails so response timing does not leak existence.
* A disabled account gets `403` (`forbidden`).
* Validation errors never echo the submitted value — passwords cannot leak back
  through an error response.
* CORS sends `allow_credentials=false` (tokens, not cookies) and an explicit
  method/header allow-list.
* `Authorization` headers and password fields are never logged.

## Profiles

Every account has a profile. `username` is required from the start: at
registration it is **derived deterministically from the email** (the local part,
lower-cased and sanitised), de-duplicated with a numeric suffix, and it can be
changed at any time through `PATCH /users/me`.

| Method  | Path                     | Auth | Description                                    |
| ------- | ------------------------ | ---- | ---------------------------------------------- |
| `GET`   | `/api/v1/users/me`       | yes  | Full profile, including owner-only fields.      |
| `PATCH` | `/api/v1/users/me`       | yes  | Edit `username`, `display_name`, `bio`, `profile_visibility`, `show_last_seen`. |
| `PATCH` | `/api/v1/users/me/privacy` | yes | The privacy subset, for a settings screen.    |
| `GET`   | `/api/v1/users/search`   | yes  | Prefix search over usernames.                   |
| `GET`   | `/api/v1/users/{username}` | yes | Another account's public profile.            |

**Username policy** (`app/core/security.py`): 3–32 characters, lowercase
letters/digits/underscores, must start with a letter or digit, and a reserved
list (`me`, `search`, `admin`, route names, …) is refused so a handle can never
collide with an endpoint. Usernames are stored lower-cased, which makes
uniqueness case-insensitive *identically* on SQLite and PostgreSQL — a unique
index on the column is the hard guarantee, the Python check is only a nicer
error.

**Two response shapes, never confused:**

* `UserPrivate` (own profile) adds `id`, `email`, `is_active`, `email_verified`,
  `show_last_seen`, `updated_at`.
* `UserPublic` (anyone else) contains only `username`, `display_name`, `bio`,
  `avatar_key`, `profile_visibility`, `created_at`, `last_seen_at`.

Neither shape has a field for the password hash, so a profile response cannot
serialise credential material.

**Protected fields.** `PATCH /users/me` uses a schema with `extra="forbid"`, so
sending `id`, `email`, `is_active`, `token_version`, `hashed_password`,
`email_verified`, `created_at` or `avatar_key` is a `422` instead of a silent
no-op. There is no API that changes `is_active` or lifts a role — those are
administrative operations for a later phase.

**Privacy.** `profile_visibility` is one of `public`, `contacts`, `private`:

| Value      | Who may view the profile                                 |
| ---------- | -------------------------------------------------------- |
| `public`   | any authenticated account                                 |
| `contacts` | accounts that have this user in **their** contact list     |
| `private`  | nobody but the owner (shown as `404`, see below)           |

`show_last_seen=false` hides `last_seen_at` from everyone except the owner.
`last_seen_at` is refreshed by an authenticated request at most once per
`LAST_SEEN_UPDATE_INTERVAL_SECONDS` (default 60) so an activity heartbeat never
becomes a write per request.

A profile the caller may not see returns **`404`, not `403`**, so the endpoint
does not confirm that the account exists. Reading another profile requires
authentication, so `contacts`-only visibility and blocks can be applied.

### Avatars (infrastructure only)

`avatar_key` holds an **opaque object-storage key** (for example
`avatars/1/0123456789abcdef0123456789abcdef.png`), never a URL. There is no
upload endpoint in this phase, so the field is **read-only through the API**: a
client cannot set it, and an attempt is a `422`. The layout
(`<namespace>/<owner-id>/<uuid>.<ext>`) is validated by
`app.core.security.validate_avatar_key`, and `app.services.profile.set_avatar_key`
is the single writer the future upload service will call. That keeps the field
portable to MinIO/S3: only the key travels through the API.

## Account security

### Email verification

| Method | Path                              | Auth | Description                    |
| ------ | --------------------------------- | ---- | ------------------------------ |
| `POST` | `/api/v1/auth/email/verify/request` | yes | Send / resend the link.       |
| `POST` | `/api/v1/auth/email/verify/confirm` | no  | Consume the token from the link. |

A verification link is also sent opportunistically at registration (a delivery
failure is logged and swallowed — registration must not fail because the mail
transport is down, and the link can be requested again).

Properties:

* the token is 32 random bytes (`secrets.token_urlsafe`); **only its SHA-256
  digest is stored**, in `verification_tokens.token_hash` (unique index),
* it expires (`EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES`) and is **single-use**
  (`used_at`),
* requesting a new link invalidates any unused one for the same purpose, so an
  older intercepted link cannot be replayed after a resend,
* unknown, expired and already-used tokens all get the same `400` body, and
* resends are throttled per account *and* per client address.

**Compatibility:** verification does **not** gate login or any existing feature
in this phase. `email_verified` is reported on the profile and can be used later
to gate specific actions (for example messaging) without breaking today's flows.

### Password change and reset

| Method | Path                                | Auth | Description                     |
| ------ | ----------------------------------- | ---- | ------------------------------- |
| `POST` | `/api/v1/auth/password/change`      | yes  | Requires the current password.  |
| `POST` | `/api/v1/auth/password/reset/request` | no | Always the same answer.         |
| `POST` | `/api/v1/auth/password/reset/confirm` | no | Consume the token and set the password. |

* The new password must satisfy the same policy as registration; a weak one is a
  `422` and the submitted value is never echoed.
* `POST /password/reset/request` returns an identical body for a registered and
  an unknown address and sends nothing for an unknown or disabled account, so it
  cannot be used to test whether an email exists.
* Reset tokens are hashed, single-use and short-lived
  (`PASSWORD_RESET_TOKEN_EXPIRE_MINUTES`, default 30) and are bound to the
  `password_reset` purpose — an email-verification token cannot be used to reset
  a password.
* Both flows advance `User.token_version`, which **invalidates every access token
  previously issued** (see below). A password change then returns a fresh token
  for the device that made the request, so the caller is not logged out of their
  own session, while a reset logs everyone out.

### Sessions and devices

Every login records a device session whose `session_id` is the access token's
`jti`. **The token itself is never stored** — no secret goes into the database,
so a database leak cannot be replayed.

| Method   | Path                             | Auth | Description                |
| -------- | -------------------------------- | ---- | -------------------------- |
| `GET`    | `/api/v1/auth/sessions`          | yes  | List my active devices.    |
| `DELETE` | `/api/v1/auth/sessions/{id}`     | yes  | End one device.            |
| `POST`   | `/api/v1/auth/logout/all`        | yes  | End every device.          |

The listed identifier is the session row's own key (never the token id), each
entry carries a `current` flag, `user_agent` is stored truncated (255 chars,
control characters stripped) and the IP is stored only when `SESSION_STORE_IP`
is enabled and is only ever returned to the account owner. Sessions belonging to
another account are reported as `404`, so they can neither be enumerated nor
revoked.

There are three independent revocation mechanisms, and a token is accepted only
if it survives all of them:

| Mechanism                 | Scope                     | Used by                                |
| ------------------------- | ------------------------- | -------------------------------------- |
| `token_blocklist` (`jti`) | one token                 | logout, single-device revocation       |
| `user_sessions.revoked_at` | one device               | device list, logout                    |
| `users.token_version` (`ver` claim) | **every** token | logout-all, password change/reset      |

`token_version` is what makes "log out everywhere" complete: it also invalidates
tokens minted *before* device sessions existed. Those older tokens have no
`ver` claim, which is read as version `0` — the column default — so they keep
working until the version is deliberately advanced. The upgrade therefore does
not break already-issued tokens.

### Email delivery configuration

`EMAIL_BACKEND` selects the transport (`app/services/email.py`):

* **`console`** (default) — development only. Renders each message to a file in
  `EMAIL_OUTBOX_DIR` (`var/outbox/`, git-ignored) instead of sending it. The
  settings refuse this backend when `APP_ENV` is not a local environment, so it
  cannot be enabled in production by accident.
* **`smtp`** — real delivery via `smtplib` (no extra dependency); configure
  `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`.
* **`disabled`** — drops messages; used by the test suite.

The token only ever exists inside the message handed to the transport: it is
**never returned by the API and never written to a normal log line**
(`redact_address` masks the recipient when logging).

**Testing the flow in development:** register (or call
`POST /auth/email/verify/request` / `POST /auth/password/reset/request`), then
open the newest file in `var/outbox/` and follow the `token=` link. The test
suite instead swaps in a capturing transport (only the transport is replaced —
token generation, hashing, expiry and single-use enforcement run for real).

## User search, contacts and blocks

### Search

`GET /api/v1/users/search?q=<prefix>&limit=&offset=`

* the term is lower-cased; anything shorter than `SEARCH_MIN_QUERY_LENGTH`
  (default 2) is refused with `400`, which blocks single-character enumeration,
* results are **prefix** matches, with an exact match ranked first, then ordered
  by username; `limit` is clamped to `SEARCH_MAX_LIMIT` (default 50),
* the prefix filter is compiled as `username >= 'ab' AND username < 'ab…'`
  rather than `LIKE 'ab%'`, so the unique index on `users.username` is usable on
  SQLite *and* PostgreSQL (a plain b-tree index cannot serve `LIKE` on
  PostgreSQL),
* the caller, deactivated accounts and anyone on either side of a block are
  excluded, and `private` profiles are never listed,
* results contain only public profile fields, and
* the endpoint is throttled per account.

### Contacts (one-directional)

| Method   | Path                        | Description                     |
| -------- | --------------------------- | ------------------------------- |
| `POST`   | `/api/v1/contacts`          | Add by username (`201`).        |
| `GET`    | `/api/v1/contacts`          | Page through my contacts.       |
| `DELETE` | `/api/v1/contacts/{username}` | Remove (`204`).               |

Adding `bob` to `alice`'s contacts creates exactly one row and **does not** make
`alice` a contact of `bob`. A mutual "connection request/accept" flow is
**not implemented** in this phase (see the roadmap) rather than shipping a
decorative state machine. The pair `(owner_id, contact_id)` is unique and a
`CHECK` constraint rejects self-relationships, so the rules hold even if
application code is bypassed. Deactivated and unknown users are both `404`.

### Blocks

| Method   | Path                       | Description        |
| -------- | -------------------------- | ------------------ |
| `POST`   | `/api/v1/blocks`           | Block by username. |
| `GET`    | `/api/v1/blocks`           | My block list.     |
| `DELETE` | `/api/v1/blocks/{username}` | Unblock.          |

A block is stored directionally but applied **symmetrically**: once it exists,
neither account can view the other's profile, add the other as a contact, or
find the other in search. Blocking yourself, duplicates, and unknown/deactivated
users are rejected.

Blocking is deliberately **non-destructive**: it does not delete an existing
contact relationship, it only hides it from the list, so unblocking restores the
previous state instead of silently losing data. The same rule now applies to
chat: a block does not delete a conversation or its history, it stops new
messages in both directions until the block is removed.

## One-to-one chat

| Method | Path                                             | Description                                  |
| ------ | ------------------------------------------------ | -------------------------------------------- |
| `POST` | `/api/v1/conversations`                          | Start (or reopen) a conversation by username. |
| `GET`  | `/api/v1/conversations`                          | Page through my conversations.                |
| `GET`  | `/api/v1/conversations/{conversation_id}`        | One conversation I belong to.                 |
| `POST` | `/api/v1/conversations/{conversation_id}/messages` | Send a message.                            |
| `POST` | `/api/v1/conversations/{conversation_id}/read` | Mark messages up to an id as read.       |
| `GET`  | `/api/v1/conversations/{conversation_id}/messages` | Chronological message history (paged).     |

### Conversations

A conversation is always between **exactly two** accounts. Its `pair_key` is the
canonical `"<lower-id>:<higher-id>"` label of the pair and carries a **unique
index**, so a duplicate pair cannot exist even when two requests race; the
service also catches the resulting `IntegrityError` and returns the row that won.

`POST /api/v1/conversations` is **idempotent**:

* `201` — a new conversation was created;
* `200` — the pair already had one, and the existing conversation is returned.

Repeating the request from either side therefore never creates a second
conversation. Talking to yourself is a `400`, an unknown or deactivated account
is a `404`, and a blocked pair is a `403` — the same vocabulary as contacts.

`GET /api/v1/conversations` returns only the caller's conversations, ordered by
most recent activity (`last_message_at`, falling back to `created_at` when no
message has been sent). Each item carries the **other** participant as a
`UserPublic` — so last-seen visibility and the absence of email apply exactly as
in profiles — plus a preview of the newest message and an `unread_count` for the
caller (messages from the other participant that have not been read).

### Messages

A message may contain **text, media, or both**; at least one is required. A
text body is plain text, trimmed before storage, **1–4000 characters**
(`MESSAGE_MAX_LENGTH` in `app/core/security.py`). Empty and whitespace-only
bodies are `422` unless media is attached (then they become a media-only
message), as is any attempt to set `sender_id`/`created_at` (the request schema
forbids unknown fields). The same limits are enforced by `CHECK` constraints on
the table, so they hold even if application code is bypassed; the nullable
`body` lets a media-only row store `NULL`.

`media_id` is optional. When present it must be an opaque id returned by the
upload endpoint for **the same conversation and the same uploader**, and must not
already be attached. A message holds at most one attachment. The response
contains `body: null` for a media-only message and a `media` object (or `null`)
alongside the existing fields.

History is returned **oldest first** and is paged with `limit`/`offset`
(`limit` is capped by `MESSAGE_MAX_LIMIT`). Ordering is by the monotonic message
`id`, which is both chronological and stable: timestamps can collide within a
second, ids cannot. Messages are append-only in this phase — there is no edit or
delete endpoint. Uploads can be attached to an existing message they belong to;
the message itself is never rewritten, only its media link is added.

### Delivery, read status and unread counts

The app is strictly one-to-one, so the recipient-level status can live on the
message itself:

| Field | Meaning |
| ----- | ------- |
| `delivered_at` | The message was pushed to a live socket of the recipient (or the recipient later marked it read). `null` while the recipient was offline. |
| `read_at` | The recipient explicitly advanced their read watermark past this message. Reading always implies delivery. |

`POST /api/v1/conversations/{conversation_id}/read` with
`{"last_message_id": 42}` marks every message **from the other participant** with
an id up to and including 42 as read (and delivered, if needed), then returns:

```json
{ "conversation_id": 1, "last_read_message_id": 42, "read_at": "…", "updated": 3, "unread_count": 0 }
```

`updated` is the number of rows that actually changed; a second identical call is
a safe no-op (`updated: 0`, `read_at: null`). The id must belong to the
conversation — an id from another conversation, or an unknown id, is `404`, not a
silent watermark move. The route uses the normal membership check and the chat
rate limiter. Every accepted read is fanned out as a `read` WebSocket event to
both participants, so tick marks and unread badges update without polling.

Sending over HTTP also fans the message out to live sockets (not just the
WebSocket send path); if the recipient has a connection, the response already has
`delivered_at` set and the sender's other devices receive a `delivered` event.
Events are only produced after the database commit.

### Authorization and blocks

Every route resolves the conversation through a single membership check. A
conversation the caller does not participate in returns **`404`, not `403`**, so
an id cannot be used to discover that a conversation exists. Sending a message
re-checks the block state because a block can be created *after* the
conversation exists:

* creating a conversation across a block → `403`;
* sending while either side has blocked the other → `403`;
* the existing conversation and its history stay readable to both participants,
  exactly like contacts survive a block and reappear on unblock.

Chat requests share one per-scope rate limiter (`CHAT_RATE_LIMIT_ATTEMPTS` /
`CHAT_RATE_LIMIT_WINDOW_SECONDS`), keyed by user id.

The conversation list is built with five queries per page (conversations,
participants, last messages, last-message media, unread counts) instead of one
per item, so paging does not turn into an N+1. Realtime delivery, delivery/read
receipts, typing indicators and unread counters are described in the next
section. Message edits and deletes remain out of scope; media attachments are
described under **Media attachments**.

## Media attachments

Photos and files are uploaded over HTTP, stored outside the database, and then
referenced from a message by an opaque id. The WebSocket never carries file
bytes; it carries the same `media` metadata as the HTTP response.

| Method   | Path                                             | Description |
| -------- | ------------------------------------------------ | ----------- |
| `POST`   | `/api/v1/conversations/{conversation_id}/media`  | Upload 1..N files to a conversation. |
| `GET`    | `/api/v1/media/{media_id}`                       | Metadata for a file I can see. |
| `GET`    | `/api/v1/media/{media_id}/content`               | Download the bytes (Bearer auth). |
| `DELETE` | `/api/v1/media/{media_id}`                       | Delete one of my unattached uploads. |
| `POST`   | `/api/v1/media/{media_id}/attach`                | Attach an upload to one of my messages. |

### Upload

The multipart field is **`files`** (repeat it for a batch). The response is
`201` with the same metadata shape used inside messages:

```json
{
  "items": [
    {
      "id": "7f1c0f4e8a2b4c6d9e0f1a2b3c4d5e6f",
      "conversation_id": 1,
      "uploader_username": "alice",
      "filename": "holiday photo.png",
      "content_type": "image/png",
      "size_bytes": 68,
      "width": 1,
      "height": 1,
      "status": "ready",
      "message_id": null,
      "created_at": "2026-10-24T12:00:00Z"
    }
  ]
}
```

```powershell
curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/conversations/1/media" `
  -H "Authorization: Bearer $env:TOKEN" `
  -F "files=@C:\path\to\photo.png;type=image/png"
```

Send text and media together, or media alone:

```json
{ "body": "look at this", "media_id": "7f1c..." }
{ "media_id": "7f1c..." }
```

Download, attach, or delete later:

```powershell
curl.exe -s "http://127.0.0.1:8000/api/v1/media/7f1c.../content" `
  -H "Authorization: Bearer $env:TOKEN" -o photo.png

curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/media/7f1c.../attach" `
  -H "Authorization: Bearer $env:TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"message_id": 42}'

curl.exe -s -X DELETE "http://127.0.0.1:8000/api/v1/media/7f1c..." `
  -H "Authorization: Bearer $env:TOKEN"
```

The storage key and internal integer id are **never** returned. `id` is 128
random bits rendered as 32 hex characters; knowing one id does not reveal any
other. `filename` is a sanitised display label only.

### Allowed formats and limits

| Category | Extensions | Detected type |
| -------- | ---------- | ------------- |
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp` | `image/jpeg`, `image/png`, `image/gif`, `image/webp` |
| Documents | `.pdf` | `application/pdf` |
| Text | `.txt`, `.csv` | `text/plain`, `text/csv` |

* Per-file size: **`MEDIA_MAX_UPLOAD_BYTES`**, default **10 MiB**.
* Files per request: **`MEDIA_MAX_FILES_PER_REQUEST`**, default **4**.
* Image width/height from the header: at most
  **`MEDIA_MAX_IMAGE_DIMENSION`**, default **20,000**.
* Empty files are `400`; an oversized file is `413`.
* The extension must be on the allow-list, the declared `Content-Type` must
  agree with the bytes (an absent type or `application/octet-stream` is
  tolerated because the bytes are still checked), and the signature must match.
* Executables, archives, HTML, SVG, scripts, unknown binary data and images
  without usable header dimensions are `415`. A PNG renamed to `.pdf` is
  rejected; a text file with invalid UTF-8 or NUL bytes is rejected.
* Filenames containing path separators, `..`, a drive/colon, control/format
  characters or a trailing dot/space are `400`. They are never used to build a
  path anyway.
* Images are **not decoded**: only bounded header fields (PNG IHDR, JPEG SOF,
  GIF logical screen, WebP VP8/VP8L/VP8X) are read. There is no Pillow
  dependency, no thumbnail, no EXIF rewriting and no decompression-bomb CPU
  cost. Consequently there is no server-side thumbnail or malware scan in this
  phase.

### Storage design

`app/services/storage.py` defines a small `StorageService` protocol
(`save`, `read`, `delete`, `exists`). `LocalStorage` is the only implementation
now: files live under `MEDIA_STORAGE_PATH` (default `var/media`, outside the
application package and ignored by Git), sharded as
`<root>/<first-2>/<next-2>/<random-key>.<detected-extension>`. Writes go to a
temporary file in the same directory and are atomically renamed; permissions
are tightened to `0600` on POSIX. A malformed key read from a corrupt database
row is rejected before it becomes a path.

Only metadata is stored in SQL (the `media` table); no BLOB column exists.
Uploads write the object first and then commit the row, and remove every object
written by the request if storage or the database fails. A process crash between
the two steps can still leave an orphan file; a periodic sweeper is the planned
operational follow-up. Deleting an unattached upload removes the object and then
the row; if the object is already missing, the row can still be cleaned up.

A future MinIO/S3 backend only needs to implement the same four methods and a
`build_storage()` branch (for example `MEDIA_STORAGE_BACKEND=s3` plus bucket
settings). The HTTP contract, database schema and message payloads stay
unchanged. No S3/MinIO service is added in this phase.

### Download, deletion and attachment

* `GET /media/{id}` and `/content` require a normal Bearer token and conversation
  membership. A non-member gets **`404`**, never `403`, so ids cannot be probed.
  A database row with a missing object also returns `404`.
* Downloads are sent as `Content-Disposition: attachment` with
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox` and
  `Cache-Control: private, no-store`. There is no `Range` support yet, so large
  files are returned in one response.
* `DELETE` is uploader-only and refuses attached media with `409`; there is no
  detach endpoint in this phase. The uploader may delete an unattached file even
  if the recipient could otherwise see the conversation.
* `POST /media/{id}/attach` with `{"message_id": 42}` attaches an owned,
  unattached file to a message **the caller sent in the same conversation**.
  Attaching twice, or attaching to a message that already has media, is `409`.
  The block rules are re-checked: a blocked pair gets `403` and the upload stays
  unattached. The change is broadcast to both participants as `message_updated`
  after the commit.
* Media routes share the `media` per-user rate limiter
  (`MEDIA_RATE_LIMIT_ATTEMPTS` / `MEDIA_RATE_LIMIT_WINDOW_SECONDS`, default
  60/60 s). Errors use the normal envelope: `400` bad filename/empty/too many
  files, `401` auth, `403` non-uploader delete or blocked attach, `404`
  hidden/missing, `409` conflict, `413` too large, `415` unsupported type, `429`
  rate limit.

## Realtime chat (WebSocket)

The authenticated socket lives at:

```text
ws://<host>/api/v1/ws        (wss:// behind TLS)
```

It is intentionally absent from the OpenAPI document (OpenAPI 3.0 cannot
describe WebSocket frames); this section is the contract.

### Authentication

The access token is **never placed in the URL** (query strings end up in proxy
and access logs). The socket is accepted first and must then send, as its very
first frame and within `WS_AUTH_TIMEOUT_SECONDS` (default 5):

```json
{ "type": "auth", "token": "<access token>" }
```

The token is resolved by the same four checks the HTTP API uses
(signature/expiry/issuer/audience, `token_version`, denylist, device session) and
the account must be active. Until it succeeds, no message is delivered and any
other frame is refused. Handshake failures close the socket with:

| Condition | Close code | Error code |
| --------- | ---------- | ---------- |
| Missing/invalid/expired/revoked token, auth timeout, non-auth first frame | `4401` | `unauthorized` / `auth_timeout` / `auth_required` / `invalid_payload` |
| Account disabled | `4403` | `forbidden` |
| Too many open sockets for the account | `4429` | `too_many_connections` |
| Too many unauthenticated sockets (global or per peer) | `1013` | `server_busy` |

The token is re-validated on every chat message **and** on a fixed interval
(`WS_TOKEN_RECHECK_INTERVAL_SECONDS`), independently of the frames the client
sends. Logout, session revocation, `token_version` changes, token expiry and
account deactivation therefore close an open socket within roughly that interval
(plus event-loop scheduling). This is a **bounded** invalidation, not an instant
push from the database: a revoked token may stay usable for up to one recheck
interval. Do not describe it as instantaneous.

### Frames

Server → client:

```json
{ "type": "ready", "connection_id": "8f…", "user": { "username": "alice", "display_name": null, "bio": null, "last_seen_at": "…", "created_at": "…", "show_last_seen": true, "profile_visibility": "public", "avatar_key": null }, "conversations": [1, 2], "server_time": "2026-10-20T12:00:00Z" }
{ "type": "message", "message": { "id": 7, "conversation_id": 1, "sender_username": "alice", "body": "hi", "media": null, "created_at": "2026-10-20T12:00:00Z", "delivered_at": "2026-10-20T12:00:01Z", "read_at": null } }
{ "type": "message_updated", "message": { "id": 7, "conversation_id": 1, "sender_username": "alice", "body": "hi", "media": { "id": "7f1c…", "content_type": "image/png", "filename": "photo.png", "message_id": 7 }, "created_at": "…", "delivered_at": "…", "read_at": null } }
{ "type": "ack", "client_message_id": "retry-1", "duplicate": false, "message": { "id": 7, "conversation_id": 1, "sender_username": "alice", "body": "hi", "media": null, "created_at": "…", "delivered_at": "…", "read_at": null } }
{ "type": "delivered", "conversation_id": 1, "message_id": 7, "delivered_at": "2026-10-20T12:00:01Z" }
{ "type": "read", "conversation_id": 1, "message_id": 7, "reader_username": "bob", "read_at": "2026-10-20T12:03:00Z" }
{ "type": "typing", "conversation_id": 1, "username": "alice", "is_typing": true, "expires_in_seconds": 5 }
{ "type": "ping", "server_time": "…" }
{ "type": "pong", "server_time": "…" }
{ "type": "error", "code": "invalid_payload", "message": "…", "client_message_id": null, "retry_after_seconds": null }
```

Client → server:

```json
{ "type": "auth", "token": "…" }
{ "type": "message", "conversation_id": 1, "body": "hi", "media_id": "7f1c…", "client_message_id": "retry-1" }
{ "type": "read", "conversation_id": 1, "last_message_id": 7 }
{ "type": "typing", "conversation_id": 1, "is_typing": true }
{ "type": "ping" }
{ "type": "pong" }
```

The `delivered` and `read` events carry a **watermark**: every message up to and
including `message_id` has that status. Clients can apply them with a single
update instead of tracking per-message rows. The `read` event is sent to both
participants' connections (the initiating socket can treat it as the
acknowledgement of its `read` frame), while `delivered` goes only to the sender's
devices. `message_updated` is emitted after media is attached to an existing
message; clients should replace the message with the same `id` rather than append
it. `typing` is forwarded only to the other participant and is **never stored**;
`is_typing: false` clears it, and `expires_in_seconds` lets a client hide a stale
indicator even if the stop frame is lost.

`typing` frames are throttled per connection (`WS_TYPING_MIN_INTERVAL_SECONDS`,
default 1 s); a stop frame is never throttled. Read frames have their own
per-connection throttle (`WS_READ_MIN_INTERVAL_SECONDS`, default 0.5 s). Both
answer an over-eager client with `rate_limited` and `retry_after_seconds` while
keeping the socket open. Neither is counted against the message budget.

`client_message_id` is optional (1–64 characters). Re-sending the same value in
the same conversation returns the stored message with `"duplicate": true`
instead of creating a second row; the unique index on
`(conversation_id, sender_id, client_message_id)` guarantees this even under a
race (the losing insert is rolled back inside a savepoint and the stored row is
returned). Only the first successful send is fanned out, so other devices do not
see a duplicate. A retry must repeat the same text **and the same `media_id`**;
switching either is a conflict, so an old key cannot be used to smuggle new
content.

**Retries versus blocks.** Access rules are evaluated *before* the idempotent
lookup. If either participant has blocked the other, a retry is answered with
`forbidden` (`403`) — it does **not** replay the old message as if the send had
just succeeded. The stored message stays in the history. Reusing a key with a
*different* body or a different attachment is rejected with `conflict` (`409`);
the first accepted payload for a key is immutable and a retry cannot smuggle new
text or media under an old key.

Frame errors never echo the payload, so a malformed auth frame cannot leak its
token into a response or a log line.

### Delivery and access

* The sender is always the authenticated account. `sender_id`, `created_at` and
  other server-owned fields are rejected (`extra="forbid"`), as are non-object
  JSON, unknown `type` values and binary frames.
* Sending goes through the same service as `POST …/messages`: optional text
  (1–4000 characters when present), optional `media_id` owned by the sender in
  the same conversation, membership check (non-members get `not_found`, never
  "forbidden") and the phase-6 block rules (a blocked pair gets `forbidden` and
  nothing is stored). The block check runs before any idempotent replay, so a
  blocked retry cannot return a success-shaped `ack`.
* An event is delivered only to connections whose accounts are members of that
  conversation, read from the database at send time — there is no "subscribe"
  frame, so a client cannot widen its audience. The initiating socket gets the
  `ack`; the other member's devices and the sender's other devices get
  `message`.
* The row is committed before the `ack`/fan-out. If the recipient is offline the
  message simply waits in the history endpoint.
* **Delivery and read state.** When a message is pushed to a live recipient
  socket, `delivered_at` is stored in the same transaction and the sender's other
  devices get a `delivered` event (the sending socket's `ack` already carries the
  timestamp). `read` frames persist the watermark and are fanned out to both
  sides; they are also accepted over HTTP. Reading sets `delivered_at` when it was
  still missing, so a message that arrives while the recipient is offline goes
  straight from "sent" to "read".
* **Typing is ephemeral.** A `typing` frame is authorized exactly like a send
  (membership, then the block rules) and forwarded only to the other
  participant's live sockets. Nothing is written to the database, and no history
  or replay contains it. Blocked pairs get `forbidden` and the indicator is not
  forwarded.
* **Last seen.** Authenticating refreshes `last_seen_at` (throttled by
  `LAST_SEEN_UPDATE_INTERVAL_SECONDS`); closing the **last** socket of an account
  records it immediately. Presence itself is never broadcast, and other accounts
  only see the timestamp when the owner's `show_last_seen` allows it — the same
  `UserPublic` rule used by profiles and conversations.
* Delivery does not rely on the `conversations` list in `ready` (which is a
  connect-time snapshot): recipients are computed from the database for every
  message, so a conversation created over HTTP while a socket is open works
  without reconnecting. Blocking or unblocking likewise takes effect on the next
  message.

### Limits, heartbeats and cleanup

* Sockets that have not authenticated yet are capped by
  `WS_MAX_PENDING_CONNECTIONS` (global, default 64) and
  `WS_MAX_PENDING_CONNECTIONS_PER_IP` (default 8); exceeding either is closed
  with `1013`/`server_busy`. The slot is released on success, failure, timeout
  or disconnect, so it cannot leak. The auth frame must arrive within
  `WS_AUTH_TIMEOUT_SECONDS` (default 5).
* At most `WS_MAX_CONNECTIONS_PER_USER` (default 5) authenticated sockets per
  account. The **new** connection is rejected; existing devices are never
  evicted.
* Messages are limited per account by `WS_MESSAGE_RATE_LIMIT_ATTEMPTS` /
  `_WINDOW_SECONDS` (default 60/60s). Exceeding it returns `rate_limited` with
  `retry_after_seconds` and keeps the socket open; pings are not counted.
* `typing` and `read` frames have separate per-connection throttles
  (`WS_TYPING_MIN_INTERVAL_SECONDS`, `WS_READ_MIN_INTERVAL_SECONDS`) so they
  cannot be turned into a database-write or fan-out loop. A typing `stop` is
  exempt so the indicator always clears.
* Raw text frames above `WS_MAX_FRAME_BYTES` (default 64 KiB) are refused before
  parsing.
* One background task per connection sends a `ping` every
  `WS_HEARTBEAT_INTERVAL_SECONDS` and re-validates the token every
  `WS_TOKEN_RECHECK_INTERVAL_SECONDS`. A client that sends nothing for
  `WS_IDLE_TIMEOUT_SECONDS` is closed (`1001`) and removed. Clients should answer
  `ping` with `pong`.
* The task is cancelled and the connection unregistered on every exit path
  (client close, network error, idle timeout, shutdown), so disconnects cannot
  leak tasks, counters or sockets. Closing the last socket of an account also
  records `last_seen_at`; a failure to write it is logged but never masks the
  close.
* A failed send to one socket only removes that socket; it cannot interrupt
  other users or the sender's other devices. On shutdown every socket is closed
  with `1001`.

### Multi-process limitation

The connection registry **and the pending counters** are in memory in one
process. With more than one uvicorn worker (or more than one server) a message
accepted by worker A is only pushed to sockets held by worker A; clients
connected elsewhere see it when they refresh history. The same applies to
`delivered`/`read`/`typing` events (the database state itself is committed, so a
history/list refresh is correct), and "last socket closed" is only known within
one worker. The pending caps apply per worker, so the effective global limit
multiplies with the worker count. Running a single worker is fine for development
and the current deployment. Scaling out needs a shared pub/sub layer (Redis
pub/sub or PostgreSQL `LISTEN/NOTIFY`) and shared counters; **Redis is
deliberately not added in this phase**.

The per-peer pending cap uses `request.client.host`. Behind a reverse proxy that
is the proxy address unless the proxy is configured to preserve the real client,
which effectively turns the per-peer cap into a global cap. Configure the proxy
before relying on it in production.

## Run the server

```powershell
uvicorn app.main:app --reload
```

| URL                                  | Description          |
| ------------------------------------ | -------------------- |
| http://127.0.0.1:8000/docs           | Swagger UI           |
| http://127.0.0.1:8000/redoc          | ReDoc                |
| http://127.0.0.1:8000/openapi.json   | OpenAPI schema       |
| http://127.0.0.1:8000/api/v1/health  | Health check         |
| http://127.0.0.1:8000/api/v1/auth/*  | Authentication       |

Example health response:

```json
{
  "status": "ok",
  "service": "Messenger API",
  "version": "0.1.0",
  "environment": "development"
}
```

## Run the tests

```powershell
python -m pytest
```

Tests never touch `messenger.db`: each one gets a temporary SQLite file. Media
tests also point `MEDIA_STORAGE_PATH` at a per-test temporary directory, so the
suite never reads or writes real user uploads. The migration tests run the real
`alembic upgrade`/`downgrade` commands against a throw-away database and
additionally assert that the migrated schema matches the ORM models (no drift);
they also verify that the `0003` upgrade backfills a unique username for every
pre-existing account and preserves existing rows, that `0004` upgrades and rolls
back cleanly, that `0006` only adds nullable status
columns (existing messages survive both directions), that `0007` rebuilds
`messages` without losing rows/foreign keys/indexes and refuses a downgrade when
media-only rows exist, and that earlier migrations are never rewritten.
Authentication tests cover registration, login, token
validation
(valid / invalid / expired), `/me`, logout and revocation, disabled accounts, the
password policy, rate limiting and the guarantee that no raw password ever
reaches the database. The later suites cover profiles and their protected
fields, email verification (issue / confirm / expiry / reuse / supersede),
password change and reset, device sessions and global invalidation, search
ranking and filtering, one-directional contacts and the cross-feature effects of
blocks. Chat tests cover conversation creation and idempotency, self/unknown/
deactivated/blocked targets, per-user listing, ordering and paging, membership
authorization on every route, message validation and length limits, stable
chronological history and paging, and the block/unblock transition. Realtime
tests open real WebSocket sessions and cover valid/invalid/expired/revoked tokens
and disabled accounts, the auth timeout and frame validation, non-members,
blocked pairs, conversation isolation, multi-device fan-out, the per-user
connection cap, the message rate limit, heartbeat/idle cleanup, idempotent
retries and the guarantee that HTTP history still works afterwards. Hardening
tests additionally cover periodic revalidation (logout, `token_version`, expiry,
deactivation) on ping-only sockets, the unauthenticated-socket caps and their
release on success/failure/timeout/disconnect, block-aware retries, key reuse
with a different body, and the unique-index race. The phase-9 suites cover
delivery/read timestamps, HTTP read watermarks and their authorization (non-members,
foreign/unknown message ids), unread counts, idempotent reads, WS-driven last seen
including the `show_last_seen` privacy gate, delivered/read fan-out to multiple
devices, typing routing and expiry fields, block suppression, membership checks
for typing, per-connection typing/read throttling, and duplicate sends not
re-emitting status events. The phase-10 suites cover PNG/JPEG/GIF/WebP/PDF/TXT/
CSV uploads, metadata and authenticated downloads, extension/signature/
declared-MIME mismatches, empty/oversized files, the configurable per-request
count, unsafe filenames, non-member and uploader-only access, deletion and
missing objects, cleanup when a database commit fails, text-plus-media and
media-only messages, attachment to an existing message, cross-conversation/
foreign/fake media, block-aware attachment, idempotent WS sends with matching
text and media, and the `message_updated` broadcast.

Email is never really sent during tests: the transport is replaced with an
in-memory capture (`tests/conftest.py`), while token creation, hashing, storage,
expiry and single-use enforcement all run against the database for real. Tests
set `JWT_SECRET_KEY` themselves, so the suite does not depend on a local `.env`.

## Configuration

Settings are managed with `pydantic-settings` and read from environment
variables or the local `.env` file. See `.env.example` for every supported key:

| Variable         | Default               | Description                                   |
| ---------------- | --------------------- | --------------------------------------------- |
| `APP_NAME`       | `Messenger API`       | Service name shown in the docs.               |
| `APP_ENV`        | `development`         | Deployment environment.                       |
| `APP_DEBUG`      | `false`               | Enable debug behaviour.                        |
| `APP_VERSION`    | `0.1.0`               | API version exposed in `/openapi.json`.        |
| `API_V1_PREFIX`  | `/api/v1`             | Prefix for version 1 routes.                   |
| `HOST` / `PORT`  | `127.0.0.1` / `8000`  | Suggested bind address.                        |
| `CORS_ORIGINS`   | *(empty)*             | Comma-separated allowed origins.               |
| `DATABASE_URL`   | `sqlite:///./messenger.db` | SQLAlchemy database URL.                  |
| `DB_ECHO`        | `false`               | Log every SQL statement.                       |
| `JWT_SECRET_KEY` | *(required)*          | HMAC signing key. **No default** — the app will not start without it. |
| `JWT_ALGORITHM`  | `HS256`               | `HS256` / `HS384` / `HS512` only.              |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15`     | Access-token lifetime (keep short).            |
| `JWT_ISSUER`     | `messenger-api`       | `iss` claim.                                   |
| `JWT_AUDIENCE`   | `messenger-api`       | `aud` claim.                                   |
| `RATE_LIMIT_ENABLED` | `true`            | Enable the login throttle.                     |
| `LOGIN_RATE_LIMIT_ATTEMPTS` | `5`      | Login attempts per window and key.             |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate-limit window in seconds.                |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Base URL used to build emailed links.     |
| `LAST_SEEN_UPDATE_INTERVAL_SECONDS` | `60` | Minimum gap between `last_seen_at` writes. |
| `SESSION_STORE_IP` | `true`            | Store the client IP on a device session (owner-visible only). |
| `SEARCH_MIN_QUERY_LENGTH` | `2`      | Shortest accepted search term.                 |
| `SEARCH_DEFAULT_LIMIT` | `20`        | Default search page size.                      |
| `SEARCH_MAX_LIMIT` | `50`            | Hard ceiling for one search page.              |
| `EMAIL_BACKEND`  | `console`             | `console` (dev only) / `smtp` / `disabled`.    |
| `EMAIL_FROM`     | `no-reply@messenger.local` | Envelope/`From:` address.                |
| `EMAIL_OUTBOX_DIR` | `var/outbox`        | Where the `console` backend writes messages.   |
| `EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES` | `1440` | Verification-link lifetime. |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | `30` | Reset-link lifetime.               |
| `SMTP_HOST` / `SMTP_PORT` | *(none)* / `587` | SMTP endpoint (only for `smtp`).     |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(none)* | SMTP credentials (only for `smtp`).      |
| `SMTP_USE_TLS`   | `true`                | STARTTLS on the SMTP connection.               |
| `EMAIL_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `3` / `3600` | Verification-email budget per account+IP. |
| `PASSWORD_RESET_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `5` / `3600` | Reset-request budget. |
| `SEARCH_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `60` / `60` | Search budget per account.      |
| `CONTACT_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `30` / `60` | Contact/block write budget.     |
| `CONVERSATION_MAX_LIMIT` | `50`          | Hard ceiling for one conversation page.        |
| `MESSAGE_MAX_LIMIT` | `100`             | Hard ceiling for one message page.             |
| `CHAT_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `120` / `60` | Chat budget per account. |
| `MEDIA_STORAGE_BACKEND` | `local`          | Media backend (only `local` in this phase). |
| `MEDIA_STORAGE_PATH` | `var/media`         | Local storage root, outside the app package. |
| `MEDIA_MAX_UPLOAD_BYTES` | `10485760`      | Per-file upload ceiling (10 MiB). |
| `MEDIA_MAX_FILES_PER_REQUEST` | `4`         | Files accepted in one multipart upload. |
| `MEDIA_MAX_IMAGE_DIMENSION` | `20000`       | Maximum image width/height read from the header. |
| `MEDIA_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `60` / `60` | Media budget per account. |
| `WS_AUTH_TIMEOUT_SECONDS` | `5`        | Seconds allowed for the first `auth` frame.    |
| `WS_HEARTBEAT_INTERVAL_SECONDS` | `30` | Server `ping` interval.                       |
| `WS_IDLE_TIMEOUT_SECONDS` | `90`        | Disconnect a client that sends nothing for this long. |
| `WS_TOKEN_RECHECK_INTERVAL_SECONDS` | `30` | Periodic token/account revalidation (bounded revocation). |
| `WS_MAX_CONNECTIONS_PER_USER` | `5`     | Authenticated sockets per account (new ones are rejected). |
| `WS_MAX_PENDING_CONNECTIONS` | `64`     | Unauthenticated sockets allowed in one process. |
| `WS_MAX_PENDING_CONNECTIONS_PER_IP` | `8` | Unauthenticated sockets per peer address.     |
| `WS_MAX_FRAME_BYTES` | `65536`          | Raw text-frame ceiling.                        |
| `WS_MESSAGE_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `60` / `60` | Realtime message budget per account. |
| `WS_TYPING_MIN_INTERVAL_SECONDS` | `1`    | Minimum gap between forwarded typing starts.   |
| `WS_TYPING_EXPIRY_SECONDS` | `5`          | Client-side lifetime of a typing indicator.    |
| `WS_READ_MIN_INTERVAL_SECONDS` | `0.5`   | Minimum gap between read frames per connection. |

Generate a signing key with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

## Error responses

Unexpected failures are normalised to a consistent JSON envelope:

```json
{ "error": { "code": "internal_error", "message": "An unexpected error occurred." } }
```

## Roadmap

Groups, shared object storage (MinIO/S3), and E2EE are planned for later
stages, and realtime fan-out needs a shared pub/sub store to run in more than one
process. When the service scales horizontally, move the rate limiters, the token
denylist and the connection registry to a shared store (for example Redis).

### Known limitations in this phase

* **Rate limiting is per process.** Each uvicorn worker (and each restarted
  process) keeps its own counters, so the effective budget multiplies with the
  worker count. It stops casual abuse, not a distributed attack; a shared store
  is the follow-up.
* **The denylist adds one query per authenticated request** (`token_blocklist`),
  and a session lookup adds another. Both are indexed and cheap, but they are
  not free; a cache belongs on the shared-store work item.
* **`request.client.host` is the proxy address** when the API runs behind one,
  which affects the stored session IP and the IP-keyed rate limits. Configure the
  proxy to overwrite `X-Forwarded-For` and read it explicitly before production.
* **Logout revokes only the presented token**, not the brother token for the same
  device; use `POST /auth/logout/all` to end every session.
* **Email verification does not gate login** in this phase, and password reset
  may take slightly longer for an unknown address than a known one (the work
  differs), which is a mild timing signal; it is throttled instead.
* **Avatars are an opaque key only** — no upload, resize or serving endpoint yet.
* **Realtime fan-out is single-process.** The WebSocket registry and the
  unauthenticated-socket counters live in one process; with several workers a
  client only receives events produced by the worker it is connected to, and the
  pending caps multiply per worker. A shared pub/sub layer and shared counters
  are the follow-up (see the realtime section). Rate limits remain per process
  for the same reason.
* **WebSocket revocation is bounded, not instantaneous.** An open socket checks
  its token every `WS_TOKEN_RECHECK_INTERVAL_SECONDS` (default 30 s) and on every
  chat message. Logout, `token_version`, expiry and deactivation therefore take
  effect within about one interval; they are not pushed by the database.
* **The per-peer pending cap depends on the proxy.** It keys on
  `request.client.host`; behind a proxy that does not preserve the client
  address, all sockets share the proxy's key.
* **Delivery/read semantics are simple and one-to-one.** `delivered_at` means
  "pushed to a live socket of the recipient, or later read"; there is no
  per-device receipt and no push of missed messages on reconnect (the client
  fetches history and marks read). "Read" is a watermark the client chooses
  (`last_message_id`), not a per-message acknowledgement. Typing indicators are
  in-process and ephemeral. Message edits and deletions are still out of scope;
  media attachments are supported and described above.
* **Media storage is local-disk and single-node.** `LocalStorage` writes under
  `MEDIA_STORAGE_PATH` on the machine running the API. There is no replication,
  shared volume, lifecycle policy or backup integration; a second worker sees the
  same directory only if the deployment mounts it there. MinIO/S3 is the planned
  backend, but no such service is added in this phase. The database/file write is
  not a distributed transaction: a crash can leave an orphan file, and there is
  no periodic sweeper yet.
* **Media processing is deliberately minimal.** Files are type-checked by
  extension, declared MIME and magic bytes, and images are bounded by header
  dimensions; they are **not decoded**, scanned for malware, stripped of EXIF or
  transcoded. There are no thumbnails, previews, resumable uploads or `Range`
  downloads. PDFs and other files are always served as attachments, never inline.
* **One attachment per message, and no detach.** The unique `media.message_id`
  makes this a database rule. Attached media cannot be deleted (it is message
  history); only unattached uploads can be removed. A richer attachment model
  (multiple files, captions, detach) would need a join table and a new contract.
* **Last-seen granularity is per process.** It is written on authentication
  (throttled by `LAST_SEEN_UPDATE_INTERVAL_SECONDS`) and when the last local
  socket closes; with multiple workers another worker's open socket is invisible,
  so the timestamp can look slightly early. It is never broadcast, and
  `show_last_seen=false` hides it from everyone else.
* **Message paging uses `limit`/`offset`.** Ordering is stable (monotonic id),
  but a page fetched while new messages arrive can shift by those new rows; a
  cursor (`before_id`) is the natural upgrade if the UI needs it.
* **A conversation is fixed to two people.** Group chats need a different
  membership model and are deliberately out of scope for this phase.
* **Blocked participants can still read their old history.** The block stops new
  messages in both directions, but it does not retroactively hide messages the
  other party already received.

### Deliberately deferred (with design)

**Two-factor authentication (TOTP)** — not implemented in this phase, and the
schema is intentionally left unchanged so it can be added without a rewrite:

1. `users.totp_secret_encrypted` (Fernet/AES-GCM, key from the environment) and
   `users.totp_enabled_at`; nothing is stored in clear text.
2. One-time recovery codes stored **hashed** in a `totp_recovery_codes` table
   (single-use, like `verification_tokens`).
3. Enrolment is a two-step handshake: `POST /auth/2fa/setup` returns the `otpauth://`
   provisioning URI, `POST /auth/2fa/enable` verifies one code before the secret
   becomes active.
4. `POST /auth/login` returns a short-lived, single-purpose *challenge* token
   (not an access token) when 2FA is on; the access token is issued only after
   `POST /auth/2fa/verify` succeeds. This is what actually protects the account —
   shipping an endpoint that still hands out a token first would be decorative.
5. Disabling 2FA requires the password again, and `revoke_all_sessions` runs
   afterwards.

**Contact requests (a mutual-connection state machine)** — this phase ships
one-directional contacts. A request flow (`pending` → `accepted` / `declined` /
`blocked`) belongs with the messaging phase, where it can be tested end to end
against the notification path, rather than as an isolated table that nothing
consumes yet. Until then, `contacts` stays simple and the block list already
provides the necessary enforcement.

