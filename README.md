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
>
> Chat, groups, file uploads and E2EE are still to come. Two-factor authentication and
> contact *requests* are deliberately deferred and documented in the roadmap.

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
│   │       └── block.py         # user-to-user blocks
│   ├── services/
│   │   ├── user.py              # registration, lookup, username derivation
│   │   ├── profile.py           # profile updates, visibility, last-seen heartbeat
│   │   ├── token.py             # revocation denylist helpers
│   │   ├── session.py           # device sessions, revoke one / all
│   │   ├── verification.py      # issue + consume email/reset tokens
│   │   ├── email.py             # EmailService: smtp / console / disabled
│   │   └── contact.py           # contacts, blocks, user search queries
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
│   │           └── blocks.py    # /api/v1/blocks
│   └── schemas/
│       ├── health.py            # response models
│       ├── auth.py              # auth request/response models
│       ├── user.py              # public vs private profile models
│       ├── account.py           # verification, password and session models
│       └── contact.py           # username reference model
├── migrations/                  # Alembic environment
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_create_health_checks.py
│       ├── 0002_create_users_and_token_blocklist.py
│       └── 0003_add_profiles_and_social.py
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
│   └── test_blocks.py           # blocks and their cross-feature effects
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

Current head: **`0003_add_profiles_and_social`**. Apply it to an existing
database (created by an earlier stage) with a single `alembic upgrade head`:

| Revision | What it adds |
| -------- | ------------ |
| `0001_create_health_checks` | `health_checks` (infrastructure). |
| `0002_create_users_and_token_blocklist` | `users`, `token_blocklist` (auth). |
| `0003_add_profiles_and_social` | Profile/security columns on `users`, plus `user_sessions`, `verification_tokens`, `contacts`, `blocks`. |

`0003` is written to be safe on a database that already contains accounts:
`users.username` is added **nullable**, every existing account is backfilled
with a deterministic handle derived from its email (de-duplicated in `id` order,
reserved names avoided), and only then is the column made `NOT NULL` and given
its unique index. Existing rows, ids and passwords are never rewritten. The
backfill is deterministic, so downgrading and re-upgrading lands on the same
names.

> **Rollback warning:** `alembic downgrade -1` (from `0003`) drops the profile,
> verification and session columns *and their data*. It is safe for a
> throw-away database and is exercised by the test suite, but do not run it on a
> production database that has real profile or session data without a backup.


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
previous state instead of silently losing data. This model does *not* by itself
make future messaging safe — the chat phase must apply the same rules again.

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

Tests never touch `messenger.db`: each one gets a temporary SQLite file. The
migration tests run the real `alembic upgrade`/`downgrade` commands against a
throw-away database and additionally assert that the migrated schema matches
the ORM models (no drift); they also verify that the `0003` upgrade backfills a
unique username for every pre-existing account and preserves existing rows.
Authentication tests cover registration, login, token validation (valid /
invalid / expired), `/me`, logout and revocation, disabled accounts, the
password policy, rate limiting and the guarantee that no raw password ever
reaches the database. The later suites cover profiles and their protected
fields, email verification (issue / confirm / expiry / reuse / supersede),
password change and reset, device sessions and global invalidation, search
ranking and filtering, one-directional contacts and the cross-feature effects of
blocks.

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

Chat/messaging, realtime transport, file storage and E2EE are planned for later
stages. When the service scales horizontally, move the login rate limiter and
the token denylist to a shared store (for example Redis).

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

