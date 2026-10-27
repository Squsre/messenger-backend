# Messenger — Backend API

Backend for the messenger project, built with **Python 3.11**, **FastAPI** and
**SQLAlchemy 2.0** with **PostgreSQL** as the only supported database, **Redis**
for disposable cache/rate-limit state, **MinIO** for private media objects, plus
**Alembic** for migrations and **Argon2id + JWT** authentication.

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
> **Stage 11** — private/public groups, owner/admin/member roles, invitations,
> member management, group messaging with per-member delivery/read receipts, and
> membership-scoped WebSocket group events.
> **Stage 12** — PostgreSQL as the only supported database: `psycopg` driver,
> a development `compose.yaml`, PostgreSQL-only settings and migrations,
> credential-redacted logs, and a test suite that runs entirely on PostgreSQL.
> **Stage 13** — Redis for a bounded public-profile cache, TTL-backed ephemeral
> data, atomic shared rate limiting across workers, fail-open/fail-closed policy,
> lifecycle-managed connections and PostgreSQL+Redis readiness checks.
> **Stage 14** — private MinIO object storage for media: least-privilege
> credentials, content-addressed metadata, short-lived presigned URLs, safe
> orphan detection/cleanup, and storage-aware readiness.
> **Stage 15** — a multi-stage, non-root backend image and a complete Compose
> stack (API + PostgreSQL + Redis + MinIO), with health checks, persistent
> volumes, controlled migrations and a production-oriented override.
>
> E2EE is still to come. Two-factor authentication and contact *requests* are
> deliberately deferred and documented in the roadmap. MinIO is the default
> media backend in Compose; local disk remains available for development and
> tests behind the same `StorageService` interface.

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
│   │   ├── rate_limit.py        # Redis-backed atomic sliding-window limiter
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
│   │       ├── conversation.py  # direct + group conversations (kind, metadata, owner)
│   │       ├── conversation_member.py # participants + group roles
│   │       ├── group_invitation.py # pending/accepted/declined/revoked invites
│   │       ├── message.py       # append-only messages + delivery/read status
│   │       ├── message_receipt.py # per-member group delivery/read receipts
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
│   │   ├── redis.py             # one process-wide Redis connection + safe URL
│   │   ├── cache.py             # fail-open get/set/delete + JSON helpers
│   │   ├── profile_cache.py     # safe public-profile cache + invalidation
│   │   ├── realtime.py          # in-process WebSocket connection registry
│   │   ├── chat.py              # conversations, membership, messages, read watermarks
│   │   ├── group.py             # groups, roles, invitations, receipts, discovery
│   │   ├── storage.py           # StorageService + local-disk and MinIO backends
│   │   ├── media_validation.py  # filename/content sniffing and dimension parsing
│   │   ├── media_orphans.py     # conservative object/database reconciliation
│   │   └── media.py             # media rows, authorization, upload/attach rules
│   ├── tools/
│   │   └── media_orphans.py     # dry-run/delete CLI for unreferenced objects
│   ├── api/
│   │   ├── deps.py              # token chain, per-scope rate limits, services
│   │   └── v1/
│   │       ├── router.py        # aggregates all v1 routers
│   │       └── endpoints/
│   │           ├── health.py    # GET  /api/v1/health and /health/ready
│   │           ├── auth.py      # /api/v1/auth/{register,login,me,logout}
│   │           ├── account.py   # /api/v1/auth/{email,password,sessions,logout-all}
│   │           ├── users.py     # /api/v1/users/{me,search,{username}}
│   │           ├── contacts.py  # /api/v1/contacts
│   │           ├── blocks.py    # /api/v1/blocks
│   │           ├── conversations.py # /api/v1/conversations
│   │           ├── groups.py    # /api/v1/groups
│   │           ├── media.py    # /api/v1/media + conversation uploads
│   │           └── realtime.py  # WS   /api/v1/ws
│   └── schemas/
│       ├── health.py            # response models
│       ├── auth.py              # auth request/response models
│       ├── user.py              # public vs private profile models
│       ├── account.py           # verification, password and session models
│       ├── contact.py           # username reference model
│       ├── chat.py              # conversation/message request + response models
│       ├── group.py             # group, member, invitation, group-message models
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
│       ├── 0007_add_media.py
│       ├── 0008_add_groups.py
│       └── 0009_media_storage_metadata.py
├── tests/
│   ├── conftest.py              # fixtures + register/login and email helpers
│   ├── test_app.py              # startup, docs, wiring
│   ├── test_health.py           # liveness endpoint
│   ├── test_redis.py            # cache, TTL, invalidation, shared rate limits
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
│   ├── test_media_messages.py  # send/attach media, history, WS, idempotency
│   ├── test_media_orphans.py   # conservative orphan detection and safe cleanup
│   ├── test_minio.py           # MinIO upload/download/URL/auth/failure integration
│   ├── test_storage.py         # storage key/config validation (no network)
│   ├── _groups.py              # group endpoint helpers (not collected)
│   ├── test_groups.py          # groups, discovery, invitations, roles, membership
│   ├── test_group_messages.py  # group messages, per-member receipts, media
│   ├── test_group_realtime.py  # group event fan-out and WebSocket authorization
│   └── test_postgres.py        # PostgreSQL connection/migration/constraint tests
├── alembic.ini
├── compose.yaml               # API + PostgreSQL 16 + Redis 7 + MinIO
├── compose.prod.yaml          # production-oriented Compose override
├── Dockerfile                 # multi-stage, non-root backend image
├── docker/minio/init.sh       # idempotent private bucket/user/policy setup
├── .dockerignore
├── .env.example
├── .gitignore
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Setup

### Option A — full stack with Docker Compose (recommended)

```powershell
# Create .env only if it does not exist. If it already exists, complete it in
# place instead of overwriting your local values.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# Edit .env: set JWT_SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD,
# MINIO_ROOT_PASSWORD and MINIO_SECRET_KEY. Never commit .env.
docker compose up --build -d

# First checkout, or after pulling a new migration, apply the schema once:
docker compose --profile tools run --rm migrate

# Verify the API and its dependencies:
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

The default command starts `postgres`, `redis`, `minio`, the one-shot
`minio-init` service and the `backend`. `backend` waits for PostgreSQL, Redis and
successful MinIO bucket initialization; it does not run migrations itself.
Use `docker compose logs -f backend` to follow API logs, and
`docker compose down` to stop the stack without deleting the PostgreSQL or MinIO
volumes. Never use `docker compose down -v` unless you intend to delete both.

The API is published on `127.0.0.1:${BACKEND_PORT:-8000}`, PostgreSQL on
`127.0.0.1:${POSTGRES_PORT:-5432}`, Redis on `127.0.0.1:${REDIS_PORT:-6379}`,
MinIO's S3 API on `127.0.0.1:${MINIO_API_PORT:-9000}` and its console on
`127.0.0.1:${MINIO_CONSOLE_PORT:-9001}`. These host ports are for development;
`compose.prod.yaml` removes the database, Redis and MinIO host ports.

### Option B — run the API locally against the Compose services

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt

# Create .env only if it does not exist. If it already exists, complete it in
# place instead of overwriting your local values.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# Start PostgreSQL, Redis and MinIO, including bucket initialization:
docker compose up -d postgres redis minio minio-init

# Apply the schema from the host:
.\.venv\Scripts\alembic upgrade head

# MEDIA_STORAGE_BACKEND=minio in .env points the host API at 127.0.0.1:9000.
.\.venv\Scripts\uvicorn app.main:app --reload
```

For a host API without MinIO, set `MEDIA_STORAGE_BACKEND=local` and keep
`MEDIA_STORAGE_PATH=var/media`; the same media API and tests then use the local
backend. The test suite itself always selects the isolated local backend unless
a MinIO test explicitly opts in.

Run the full test suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Database

PostgreSQL 16 is the **only** supported development and deployment database.
`DATABASE_URL` is required and must use the `postgresql+psycopg://` driver from
`requirements.txt`; SQLite URLs are rejected by the settings layer and the
engine factory. There is no file-database fallback.

The test suite also runs on PostgreSQL, but uses a separate database named by
`TEST_DATABASE_URL` (normally `messenger_test`). Each test creates and drops its
own short-lived schema, so tests never touch the development database. The test
database is created automatically if it does not exist.

`POSTGRES_*` values initialize a new Docker volume. Changing them after the first
startup does not update the existing role or database; update PostgreSQL
manually after a backup, or deliberately use a new volume.

Set the matching SQLAlchemy URL in `.env`:

```dotenv
POSTGRES_DB=messenger
POSTGRES_USER=messenger
POSTGRES_PASSWORD=your-local-password
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://messenger:your-local-password@127.0.0.1:5432/messenger
```

URL-encode special characters in a password. `postgres://` and
`postgresql://` shorthands are normalised to `postgresql+psycopg://` by the
settings layer. The real password belongs only in the Git-ignored `.env`;
`.env.example` contains a clearly marked development placeholder.

### Compose services

`compose.yaml` runs the API, PostgreSQL, Redis, MinIO and the one-shot
`minio-init` initializer. It intentionally does not add Celery or other side
services that the current code does not use.

```powershell
# .env is read automatically by Docker Compose
docker compose up --build -d
docker compose ps
docker compose logs -f postgres redis minio backend
```

PostgreSQL uses `postgres:16-alpine`, a named volume
(`messenger_postgres_data`), the `POSTGRES_DB` / `POSTGRES_USER` /
`POSTGRES_PASSWORD` / `POSTGRES_PORT` variables from `.env`, and a
`pg_isready` healthcheck. Compose refuses to start if `POSTGRES_PASSWORD`,
`REDIS_PASSWORD`, `MINIO_ROOT_PASSWORD`, `MINIO_SECRET_KEY` or another required
credential is unset. `docker compose down` stops the containers and keeps
the database volume; `docker compose down -v` deletes the database volume and
must be used deliberately.

Redis uses `redis:7-alpine`, a password from `REDIS_PASSWORD`, a 256 MB
`allkeys-lru` ceiling, persistence disabled (it only holds disposable data) and
an authenticated `redis-cli ping` healthcheck. It deliberately has no volume:
losing cache entries or rate-limit counters is harmless.

### MinIO (private media)

MinIO is the durable media backend in Compose. The server uses
`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`; the API uses the separate
`MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` user created by `minio-init`. The
initializer also creates the private `MINIO_BUCKET` and attaches a
least-privilege policy limited to that bucket. Anonymous access is explicitly
disabled, and the API never receives the root credentials. The example
`MINIO_SECRET_KEY` placeholder is rejected when `APP_ENV` is not a local
environment, just like the example JWT secret.

The backend connects to `http://minio:9000` inside Compose. Presigned URLs are
signed with `MINIO_PUBLIC_ENDPOINT` (default `127.0.0.1:9000`), because a URL
containing the internal service name would not work from the host. If you change
`MINIO_API_PORT`, update `MINIO_PUBLIC_ENDPOINT` to the same host:port (and set
`MINIO_SECURE=true` when the public endpoint is HTTPS). Only the
authenticated `GET /media/{id}/url` route issues a URL, after the normal
membership/state checks, and the URL lifetime is
`MINIO_PRESIGNED_URL_EXPIRES_SECONDS` (30–3600 seconds, default 300). The
`GET /media/{id}/content` streaming route remains available for clients that do
not want a direct URL.

MinIO data is stored in the named volume `messenger_minio_data`. The API also
mounts `messenger_media_data` for the local-storage fallback and
`messenger_outbox_data` for the development console email backend. MinIO uses
the pinned official image digest; `minio-init` uses the pinned `minio/mc` digest.

### Controlled migrations

Migrations are deliberately **not** executed by backend startup. Run them once
from a separate one-shot container (or from the host, as in Option B):

```powershell
docker compose --profile tools run --rm migrate
docker compose run --rm backend alembic current
```

`migrate` waits for PostgreSQL, applies `alembic upgrade head` and exits. It is
excluded from the default service set by the `tools` profile, so a normal
`docker compose up` cannot run migrations concurrently with another backend
replica. For a production deployment, run the same command as a release step
before rolling out the new image.

### Production-oriented override

`compose.prod.yaml` removes the PostgreSQL, Redis and MinIO host ports, sets
`APP_ENV=production`/`APP_DEBUG=false`, requires an externally reachable
`MINIO_PUBLIC_ENDPOINT` and requires an explicit `CORS_ORIGINS` value:

```powershell
docker compose -f compose.yaml -f compose.prod.yaml up --build -d
```

The API remains bound to loopback for a reverse proxy/TLS terminator on the host.
Use a production `EMAIL_BACKEND` (`smtp` or `disabled`) and real secrets; the
development `console` backend is rejected when `APP_ENV=production`. The
override is intentionally small: database backups, TLS termination, secret
management and off-host MinIO replication remain deployment responsibilities.

### Redis (cache, shared throttling and ephemeral data)

Redis is **not** a source of truth. PostgreSQL remains authoritative; Redis
stores only a bounded public-profile cache and the shared rate-limit counters.
The application creates one process-wide thread-safe Redis client (and its
connection pool), pings it during startup, and closes it during shutdown.

Set both URLs in `.env` (the password is URL-encoded):

```dotenv
REDIS_PASSWORD=your-local-password
REDIS_PORT=6379
REDIS_URL=redis://:your-local-password@127.0.0.1:6379/0
TEST_REDIS_URL=redis://:your-local-password@127.0.0.1:6379/15
```

The test suite refuses to run if `TEST_REDIS_URL` points at the same Redis
database as `REDIS_URL`. It gives every test a unique `REDIS_KEY_PREFIX` and
deletes only that prefix; it never calls `FLUSHDB` or touches development keys.

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

The database URL is read from `DATABASE_URL` (via `.env`) and must be a
PostgreSQL URL. Stage 13 adds no revision; the schema is still the phase-11
head, now verified from an empty PostgreSQL database through every upgrade and
back down again.

> PostgreSQL note: Alembic 1.14 hardcodes `alembic_version.version_num` as
> `VARCHAR(32)`, but this project's revision ids are longer (for example
> `0002_create_users_and_token_blocklist`). `migrations/env.py` widens that
> bookkeeping table to `VARCHAR(255)` before the first migration and repairs an
> existing narrow table in place. This changes only Alembic metadata, never an
> application table or migration history.

Current head: **`0009_media_storage_metadata`**. Apply it to an existing
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
| `0008_add_groups` | Group columns on `conversations` (`kind`, `name`, `description`, `visibility`, `owner_id`), `conversation_members.role`, `group_invitations`, and `message_receipts`. |
| `0009_media_storage_metadata` | `media.storage_backend`, `media.checksum_sha256` and `media.etag`, plus a CHECK constraint limiting the backend to `local` or `minio`. Existing rows are backfilled as `local`. |

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
message can be media-only. PostgreSQL changes the column and replaces the CHECK
constraints in place while preserving every row, primary key, foreign key and
index. `media` is created after that change, and the upgrade is safe on a
populated database. Downgrading refuses if any `body IS NULL` row exists
instead of deleting messages or inventing placeholder text; add text to those
messages first or restore a backup.

`0008` reuses `conversations` and `messages` for groups. Existing rows are
backfilled as `kind='direct'` and role `member`; no message is rewritten.
`pair_key` stays non-null and unique, with a random `group:<hex>` value for group
rows, so the parent table does not need a risky rebuild. The migration adds
`group_invitations` (with a partial unique index for one pending invitation
per group/invitee) and `message_receipts` (unique per message/member). The
downgrade **refuses** while any group conversation exists instead of deleting
group memberships, invitations, receipts or messages.

`0009` is additive: it adds `storage_backend` (with a server default of
`local`), nullable `checksum_sha256` and `etag` columns, and a CHECK constraint
for the two supported backends. Existing media rows are backfilled as `local`;
the object bytes remain in the backend and no BLOB is added to PostgreSQL.

> **Rollback warning:** from `0009`, `alembic downgrade -1` removes only the
> storage metadata columns; `-2` removes the group schema only when no group
> conversations exist; `-3` then drops `media` and restores
> `messages.body NOT NULL` (refusing if media-only messages exist); `-4`
> removes the status timestamps; `-5` removes the idempotency key. `-6` drops
> the three chat tables *and the messages they hold*; `-7` drops the profile,
> verification and session columns *and their data*. All of these are exercised
> by the test suite, but do not run them on a production database that has real
> data without a backup.


### Verify the PostgreSQL schema

```powershell
# From an empty database to the current head
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\alembic current
.\.venv\Scripts\alembic check

# Optional destructive check on a throw-away database
.\.venv\Scripts\alembic downgrade base
.\.venv\Scripts\alembic upgrade head
```

Expected: `current` reports `0009_media_storage_metadata (head)` and `check`
reports `No new upgrade operations detected.` The PostgreSQL integration tests
(see [Run the tests](#run-the-tests)) repeat this against isolated schemas and
compare the migrated schema with `Base.metadata`.

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

### PostgreSQL connection

`DATABASE_URL` is the only database selection setting. It must point at the
same database, user, password and host port configured by the `POSTGRES_*`
values used by Compose. The engine uses SQLAlchemy's normal pool with
`pool_pre_ping=True`; startup logs print the URL with the password redacted.

**Existing data:** migrations create the PostgreSQL schema from empty. This
phase intentionally does not import data from the former local file database;
that local file was removed as part of this phase. For a real deployment, take
and verify backups before any schema change.

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
shared database is required once the API runs on several hosts. Redis is now
available for cache and throttling, but caching revoked `jti` values (and the
invalidation edge cases that come with it) remains a separate follow-up.

### Shared rate limiting

All throttled endpoints use one Redis-backed sliding-window implementation
(`app/core/rate_limit.py`). `POST /login` is keyed by **client IP** *and*
**target account**, so neither one host guessing many accounts nor many hosts
guessing one account goes unnoticed. Limits: 5 attempts / 60 s
(`LOGIN_RATE_LIMIT_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_SECONDS`). Exceeding a
limit returns `429` with a `Retry-After` header.

The prune/count/insert/expire operation runs as a single Lua script, so counters
are atomic and shared by every worker and host using the same Redis database.
Redis's clock is used, avoiding process clock skew. Counters expire with the
window and are disposable. Account and address identifiers are hashed before
they become key material, so raw e-mail addresses and IPs are not stored in
Redis.

If Redis cannot answer, `RATE_LIMIT_FAILURE_POLICY` decides the outcome:

* `closed` (default, safer): return `503 service_unavailable` instead of
  silently allowing unlimited login attempts;
* `open`: allow the request, favouring availability over throttling.

WebSocket message throttling follows the same policy: with `closed`, a Redis
outage rejects the message with a `service_unavailable` error but keeps the
socket open; with `open`, the message is allowed.

Set `RATE_LIMIT_ENABLED=false` to disable throttling entirely (for example
behind an external gateway that already enforces it). The cache is always
fail-open: a Redis outage only causes a cache miss.

### Public-profile cache

`GET /users/{username}` may serve a cached public profile for
`PROFILE_CACHE_TTL_SECONDS`. Only profiles with `profile_visibility=public` and
`show_last_seen=true` are cached; private/contacts-only profiles and hidden
last-seen values never enter Redis. PostgreSQL remains authoritative for
existence, active state, visibility and blocks, so those checks run before a
cached entry is used. Profile edits invalidate the current key, and a username
change invalidates both the old and the new key. Tokens, passwords, email
addresses, account ids and private profile fields are never cached.

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
uniqueness case-insensitive in PostgreSQL — a unique index on the column is the
hard guarantee, the Python check is only a nicer error.

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
  rather than `LIKE 'ab%'`, so the unique index on `users.username` is usable in
  PostgreSQL (a plain b-tree index cannot serve `LIKE`),
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
| `GET`    | `/api/v1/media/{media_id}/url`                   | Short-lived presigned URL (MinIO only). |
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
      "checksum_sha256": "f0e1…",
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

With the MinIO backend, an authorized member can ask for a short-lived direct
URL instead of streaming through the API:

```powershell
curl.exe -s "http://127.0.0.1:8000/api/v1/media/7f1c.../url" `
  -H "Authorization: Bearer $env:TOKEN"
# {"media_id":"7f1c...","url":"http://127.0.0.1:9000/...","expires_in_seconds":300}
```

The URL is issued only after the same membership/state check as `/content`, is
marked `Cache-Control: private, no-store`, and must not be logged or shared.
`/content` remains the portable download path and is the only option for the
local backend (where `/url` returns `409`). A storage outage returns `503`; a
missing object returns `404`.

The storage key and internal integer id are **never** returned in metadata.
`id` is 128 random bits rendered as 32 hex characters; knowing one id does not
reveal any other. `filename` is a sanitised display label only. The presigned URL
necessarily contains the random object name for its short lifetime; it never
contains a user filename or a database id.

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

`app/services/storage.py` defines a `StorageService` protocol
(`save`, `read`, `delete`, `exists`, `presigned_url`, `iter_objects`, `health`).
Two implementations share the same HTTP contract:

* `LocalStorage` writes under `MEDIA_STORAGE_PATH` (default `var/media`, outside
  the application package and ignored by Git), sharded as
  `<root>/<first-2>/<next-2>/<random-key>.<detected-extension>`. Writes use a
  temporary file and an atomic rename; permissions are tightened to `0600` on
  POSIX.
* `MinioStorage` writes to the private `MINIO_BUCKET`. Object names are
  `<MINIO_KEY_PREFIX><random-key>.<detected-extension>`, so a test or deployment
  can isolate itself without a second bucket. The key prefix is validated and a
  malformed database key is rejected before it becomes an object name.

Only metadata is stored in SQL (the `media` table); no BLOB column exists. The
`media` row records the backend, storage key, original display filename, detected
MIME type, size, dimensions, status, SHA-256 checksum, optional backend ETag,
conversation, uploader and optional message. Existing rows from the local phase
are migrated with `storage_backend='local'`.

Uploads write the object first and then commit the row, and remove every object
written by the request if storage or the database fails. Deleting an unattached
upload removes the object and then the row; if the object is already missing,
the row can still be cleaned up. A process crash between the object write and
the database commit can still leave an orphan, so the repository ships a
conservative reconciler:

```powershell
# Dry run: report objects with no media row, recent untracked objects, and rows
# whose object is missing. Nothing is deleted.
python -m app.tools.media_orphans --grace-seconds 3600

# Delete only objects older than the grace period and absent from every media row.
python -m app.tools.media_orphans --grace-seconds 3600 --delete
```

The grace period protects an upload that is still between object write and
database commit. The tool never deletes database rows, never touches objects
outside the configured prefix, and re-checks every candidate against the
database immediately before deletion. Missing-object reporting is scoped to
rows whose `storage_backend` matches the configured backend. Run it as an
operational job, not from request handling.

### Download, deletion and attachment

* `GET /media/{id}`, `/content` and `/url` require a normal Bearer token and
  conversation membership. A non-member gets **`404`**, never `403`, so ids
  cannot be probed. For `/content` and `/url`, a database row whose object is
  missing also returns `404`; metadata remains readable for diagnosis.
* `/url` is MinIO-only. It signs a URL after authorization, with a lifetime from
  `MINIO_PRESIGNED_URL_EXPIRES_SECONDS`; the local backend returns `409`. The
  URL response is `Cache-Control: private, no-store`; never log it.
* Downloads are sent as `Content-Disposition: attachment` with
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox` and
  `Cache-Control: private, no-store`. There is no `Range` support yet, so large
  files are returned in one response. Presigned URLs also request an attachment
  disposition, but the API does not proxy or inspect those bytes.
* `DELETE` is uploader-only and refuses attached media with `409`; there is no
  detach endpoint in this phase. The uploader may delete an unattached file even
  if the recipient could otherwise see the conversation.
* `POST /media/{id}/attach` with `{"message_id": 42}` attaches an owned,
  unattached file to a message **the caller sent in the same conversation**.
  Attaching twice, or attaching to a message that already has media, is `409`.
  The block rules are re-checked for direct chats: a blocked pair gets `403` and
  the upload stays unattached. The change is broadcast to every conversation
  member as `message_updated` after the commit.
* Media routes share the `media` per-user rate limiter
  (`MEDIA_RATE_LIMIT_ATTEMPTS` / `MEDIA_RATE_LIMIT_WINDOW_SECONDS`, default
  60/60 s). Errors use the normal envelope: `400` bad filename/empty/too many
  files, `401` auth, `403` non-uploader delete or blocked attach, `404`
  hidden/missing, `409` conflict/direct-URL unsupported, `413` too large, `415`
  unsupported type, `429` rate limit, `503` storage backend unavailable.

## Groups

Stage 11 adds private and public groups without introducing a second chat
pipeline. A group is still a `conversations` row, its members are still
`conversation_members` rows, and its messages are still `messages` rows. Direct
chat response shapes are unchanged; group routes use their own response models
because delivery/read state is per member rather than per message.

### Roles, visibility and ownership

| Role | Can do |
| ---- | ------ |
| `owner` | Edit all group fields, change visibility, invite, remove admins/members, change roles, transfer ownership. Cannot leave or be removed while owning the group. |
| `admin` | Edit name/description, invite, remove ordinary members. Cannot remove another admin or the owner, change roles, or change visibility. |
| `member` | Read/send messages, view the member list, leave. |

* **Private** is the default. A private group never appears in search; a
  non-member gets `404` for its profile, member list and messages. Joining
  requires a valid invitation.
* **Public** groups appear in `GET /groups/search` and may be joined directly.
  Their profile and member list are visible to authenticated users, but reading
  or sending messages still requires membership.
* The owner is always a member with the `owner` role. Ownership is transferred
  with a dedicated endpoint; the old owner becomes an `admin`. This is what keeps
  a group from being left without an owner.
* Roles and user ids in request bodies are never trusted. The service reloads
  the caller's membership row and checks the role server-side.
* Blocks prevent invitations (and a blocked account joining a public group owned
  by the blocker). They do not retroactively remove an existing group member or
  suppress a multi-party group message.

### Endpoints

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `POST` | `/api/v1/groups` | Create a group; the caller becomes owner. |
| `GET` | `/api/v1/groups` | Page through my groups, newest activity first. |
| `GET` | `/api/v1/groups/search?q=…` | Search public group names/descriptions. |
| `GET` | `/api/v1/groups/{id}` | Group profile (private: members only). |
| `PATCH` | `/api/v1/groups/{id}` | Edit name/description (manager); visibility (owner). |
| `GET` | `/api/v1/groups/{id}/members` | Paged member list, owner/admins first. |
| `POST` | `/api/v1/groups/{id}/join` | Join a public group; repeat is `200`. |
| `POST` | `/api/v1/groups/{id}/invitations` | Invite by `username` or `user_id`. |
| `GET` | `/api/v1/groups/{id}/invitations` | Manager invitation list. |
| `GET` | `/api/v1/groups/invitations` | My invitations; optional `status`. |
| `POST` | `/api/v1/groups/{id}/invitations/{invitation_id}/accept` | Accept my invitation. |
| `POST` | `/api/v1/groups/{id}/invitations/{invitation_id}/decline` | Decline my invitation. |
| `POST` | `/api/v1/groups/{id}/invitations/{invitation_id}/revoke` | Revoke a pending invitation (manager). |
| `DELETE` | `/api/v1/groups/{id}/invitations/{invitation_id}` | DELETE alias for revoke. |
| `DELETE` | `/api/v1/groups/{id}/members/{user_id}` | Remove a member (manager). |
| `PATCH` | `/api/v1/groups/{id}/members/{user_id}` | Change role to `admin`/`member` (owner). |
| `POST` | `/api/v1/groups/{id}/transfer-owner` | Transfer ownership to a current member (owner). |
| `POST` | `/api/v1/groups/{id}/leave` | Leave; the owner must transfer first. |
| `POST` | `/api/v1/groups/{id}/messages` | Send a group message. |
| `GET` | `/api/v1/groups/{id}/messages` | Group message history (members only). |
| `POST` | `/api/v1/groups/{id}/read` | Advance my per-member read watermark. |

Group creation returns `201`; the first join returns `201` and a repeated join
returns `200`. Remove/leave/revoke-by-DELETE return `204`. A private group the
caller cannot see, a missing member, or a missing invitation all return `404`
rather than confirming existence. Invalid role changes, duplicate memberships,
non-pending invitation transitions and an owner trying to leave return `409` or
`403` according to the role/state rule.

### Creating, listing and searching

`POST /groups` accepts `{"name": "...", "description": "...", "visibility":
"private"|"public"}`. Names are trimmed, must be 1–64 characters and cannot
contain control characters; descriptions are optional up to 280 characters.
Responses include `owner_username`, `member_count`, `is_member`, the caller's
`role`, `unread_count`, and (for members) a newest-message preview.

`GET /groups/search` only returns public groups. Search terms are trimmed and
must meet the configured `SEARCH_MIN_QUERY_LENGTH` (default 2); `%` and `_` are
escaped, so a term is matched literally. Private group rows are filtered in SQL,
not hidden only in the response builder.

### Invitations

Invitations are scoped to one group and one invitee and move through
`pending` → `accepted` / `declined` / `revoked`. Only a pending invitation can be
accepted, declined or revoked. A partial unique index permits at most one
*pending* invitation per `(group, invitee)`, so re-inviting is possible after a
decline or revoke. Accepting twice after membership is idempotent; accepting
another account's invitation returns `404`.

Only the invitee may accept or decline. Owners and admins may invite, list and
revoke. Self-invites, existing members, blocked accounts and inactive accounts
are rejected. The invitee receives a `group_invitation` WebSocket event; the
inviter/owner receive status updates.

### Group messages and per-member receipts

Group messages use the existing message validation (text 1–4000 characters,
optional media uploaded by the sender in the same conversation, one attachment
per message). Media routes work for groups because membership is checked against
the shared conversation table; the direct-chat block check is skipped for group
attachments.

The direct chat model stores one `delivered_at`/`read_at` pair per message because
there is one recipient. That would be wrong for a group: one member reading must
not mark the message read for everyone. Group messages therefore use
`message_receipts`, one row per `(message, member)`, and the group message
response exposes:

* `read_count` / `delivered_count` — other members who have read/received it;
* `member_count` — total members, including the sender;
* `read_by_me` / `delivered_to_me` — the caller's own receipt state (always true
  for the caller's own messages);
* `delivered_at` / `read_at` — retained for client compatibility but `null` for
  groups; use the counts/flags instead.

`POST /groups/{id}/read` with `{"last_message_id": 42}` advances only the
caller's watermark, marks every incoming message up to that id delivered/read,
and returns the same `ReadReceipt` shape as direct chat. Unread counts on the
group list are computed from this table, not from the message columns.

### Group realtime events

Group events use the existing authenticated WebSocket. Message fan-out still
uses the normal `message` event; only current members receive it. Read frames for
a group update `message_receipts` and fan a normal `read` event out to the group's
members. Typing is forwarded to the other members.

Additional event types are:

`group_created`, `group_updated`, `group_member_joined`, `group_member_left`,
`group_member_removed`, `group_role_updated`, `group_owner_transferred`,
`group_invitation`, `group_invitation_updated`.

The audience is always read from `conversation_members` at broadcast time. A
removed/left member stops receiving group events immediately; a non-member never
receives a group message, typing or read event. Invitation events go only to the
invitee and the inviter/owner, because the invitee is not a member yet.

### Manual Swagger walkthrough

1. Register and log in as `alice`, `bob` and `carol`; use **Authorize** with
   Alice's access token.
2. `POST /api/v1/groups` with a private group; note the returned `id`.
3. `POST /api/v1/groups/{id}/invitations` with `{"username": "bob"}`.
4. Authorize as Bob, `GET /api/v1/groups/invitations`, then
   `POST /api/v1/groups/{id}/invitations/{invitation_id}/accept`.
5. As Bob, `POST /api/v1/groups/{id}/messages`; as Alice, read
   `GET /api/v1/groups/{id}/messages` and `GET /api/v1/groups`.
6. As Bob, `POST /api/v1/groups/{id}/read`; re-read the history as Alice and
   check `read_count` changed for Bob only.
7. As Alice, `PATCH /api/v1/groups/{id}/members/{bob_user_id}` with
   `{"role": "admin"}`, then `POST /api/v1/groups/{id}/transfer-owner` with
   `{"username": "bob"}`. Alice can now `POST /leave`; Bob cannot until he
   transfers ownership.
8. Create a public group, search it as Carol, `POST /join`, and send a message.
9. Negative checks: as Carol, a private group's detail/members/messages return
   `404`; an ordinary member (not owner/admin) inviting someone returns `403`;
   a duplicate pending invite returns `409`; the owner's `POST /leave` returns
   `409`.
10. WebSocket authorization needs a real socket (Swagger cannot send frames):
    connect two accounts to `/api/v1/ws`, join the same group, and verify only
    members receive `message`, `read`, `typing` and group event types.

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

Group events use the same envelope and are sent only to the members authorized
at broadcast time:

```json
{ "type": "group_created", "group": { "id": 4, "name": "Hikers", "visibility": "private", "owner_username": "alice", "member_count": 1, "role": "owner" } }
{ "type": "group_member_joined", "group_id": 4, "member": { "user_id": 2, "username": "bob", "role": "member" } }
{ "type": "group_role_updated", "group_id": 4, "member": { "user_id": 2, "username": "bob", "role": "admin" } }
{ "type": "group_owner_transferred", "group_id": 4, "owner": { "user_id": 2, "username": "bob", "role": "owner" }, "previous_owner_username": "alice" }
{ "type": "group_invitation", "invitation": { "id": 9, "group_id": 4, "group_name": "Hikers", "status": "pending" } }
```

The `delivered` and `read` events carry a **watermark**: every message up to and
including `message_id` has that status. Clients can apply them with a single
update instead of tracking per-message rows. In a direct chat the `read` event is
sent to both participants' connections (the initiating socket can treat it as the
acknowledgement of its `read` frame), while `delivered` goes only to the sender's
devices. For a group, `read` is sent to every current member and refers to the
initiating member's own receipt; there is no group-wide `delivered` event because
delivery is per member. `message_updated` is emitted after media is attached to an
existing message; clients should replace the message with the same `id` rather
than append it. `typing` is forwarded to the other participant(s) and is **never
stored**; `is_typing: false` clears it, and `expires_in_seconds` lets a client hide
a stale indicator even if the stop frame is lost.

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
  "forbidden") and, for direct chats, the phase-6 block rules (a blocked pair gets
  `forbidden` and nothing is stored). Group messages are multi-party, so a
  pairwise block does not suppress them; blocks are enforced when inviting or
  joining a public group. For direct chats the block check runs before any
  idempotent replay, so a blocked retry cannot return a success-shaped `ack`.
* An event is delivered only to connections whose accounts are members of that
  conversation, read from the database at send time - there is no "subscribe"
  frame, so a client cannot widen its audience. The initiating socket gets the
  `ack`; the other members' devices and the sender's other devices get
  `message`.
* The row is committed before the `ack`/fan-out. If a recipient is offline the
  message simply waits in the history endpoint.
* **Delivery and read state.** In a direct chat, when a message is pushed to a
  live recipient socket, `delivered_at` is stored in the same transaction and the
  sender's other devices get a `delivered` event (the sending socket's `ack`
  already carries the timestamp). In a group, a `message_receipts` row is written
  for each connected recipient instead; the group HTTP history exposes counts.
  `read` frames persist the caller's watermark and are fanned out to the
  conversation's members; they are also accepted over HTTP. Reading sets
  `delivered_at` when it was still missing, so a message that arrives while the
  recipient is offline goes straight from "sent" to "read".
* **Typing is ephemeral.** A `typing` frame is authorized exactly like a send
  (membership, then the direct-chat block rules) and forwarded only to the other
  member(s)' live sockets. Nothing is written to the database, and no history or
  replay contains it. In a direct chat, blocked pairs get `forbidden` and the
  indicator is not forwarded; group typing is not suppressed by a pairwise
  block.
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
and the current deployment. Rate-limit counters are already shared through Redis;
WebSocket fan-out still needs a shared pub/sub layer (Redis pub/sub or
PostgreSQL `LISTEN/NOTIFY`), which is deliberately out of scope for this phase.

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
| http://127.0.0.1:8000/api/v1/health  | Liveness check       |
| http://127.0.0.1:8000/api/v1/health/ready | PostgreSQL + Redis + media storage readiness |
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

Readiness (`/api/v1/health/ready`) checks PostgreSQL, Redis and the configured
media backend (MinIO bucket presence or a writable local directory), returning
`200` or `503`:

```json
{
  "status": "ok",
  "checks": {"database": "ok", "redis": "ok", "storage": "ok"}
}
```

The Compose `backend` healthcheck uses this endpoint, so a missing bucket or an
unreachable MinIO service marks the container unhealthy rather than silently
accepting uploads that cannot be stored.

## Run the tests

```powershell
# PostgreSQL, Redis and MinIO (with its bucket initialized) must be healthy.
docker compose up -d postgres redis minio minio-init
.\.venv\Scripts\python.exe -m pytest
```

Tests run against isolated PostgreSQL and Redis resources. Each test gets a
unique short-lived PostgreSQL schema in the database named by
`TEST_DATABASE_URL` (normally `messenger_test`), which is dropped afterwards. The
development database named by `DATABASE_URL` is never used by the suite. Local
media tests point `MEDIA_STORAGE_PATH` at a per-test temporary directory, so the
suite never reads or writes real user uploads.

`tests/test_minio.py` uses a unique `MINIO_KEY_PREFIX` per test and deletes only
that prefix. It probes the configured MinIO bucket first; if MinIO is unavailable
or the bucket is missing, those tests are **skipped explicitly** (shown as `s` in
the summary), never reported as passed. The rest of the suite remains runnable
without MinIO.

`TEST_DATABASE_URL` is required and must point at a **separate** disposable
database; the test fixtures refuse to run if it matches `DATABASE_URL`. If the
test database does not exist, the suite creates it automatically. The
PostgreSQL integration tests in `tests/test_postgres.py` are part of the normal
suite and are not skipped.

Redis tests use `TEST_REDIS_URL`, which must point at a different Redis database
from `REDIS_URL` (database 15 by default). Every test receives a unique key
prefix and the fixture deletes only that prefix afterwards; no test calls
`FLUSHDB`. Redis must be running (`docker compose up -d redis`) or the suite
stops with a clear configuration error.

The tests cover the psycopg connection, `Base.metadata.create_all`, a full
`alembic upgrade head` from empty, `alembic check`-equivalent metadata
comparison, downgrade to base, the pending-invitation partial unique index,
CHECK constraints and the one-receipt-per-member rule.

The migration tests run the real `alembic upgrade`/`downgrade` commands against
a throw-away schema and additionally assert that the migrated schema matches
the ORM models (no drift); they also verify that the `0003` upgrade backfills a
unique username for every pre-existing account and preserves existing rows,
that `0004` upgrades and rolls back cleanly, that `0006` only adds nullable
status columns (existing messages survive both directions), that `0007` alters
`messages` without losing rows/foreign keys/indexes and refuses a downgrade when
media-only rows exist, that `0009` backfills existing media as `local` and adds
the storage/checksum/ETag columns, and that earlier migrations are never
rewritten.
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
text and media, and the `message_updated` broadcast. The MinIO suites add
upload/download through the real bucket, presigned-URL authorization and expiry
metadata, checksum/backend/ETag persistence, invalid/oversized uploads storing
nothing, storage-outage `503` rollback, storage-aware readiness, conservative
orphan scanning (including backend-scope safety), and storage key/config
validation without network access.

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
| `BACKEND_PORT` / `UVICORN_WORKERS` | `8000` / `1` | Compose host port and worker count. Keep one worker while the WebSocket registry is in-process. |
| `CORS_ORIGINS`   | *(empty)*             | Comma-separated allowed origins.               |
| `DATABASE_URL`   | *(required)*          | PostgreSQL URL: `postgresql+psycopg://...`; SQLite is rejected. |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_PORT` | *(compose values)* | PostgreSQL container settings; must match `DATABASE_URL`. |
| `TEST_DATABASE_URL` | *(required for tests)* | Separate disposable PostgreSQL database for the test suite. |
| `DB_ECHO`        | `false`               | Log every SQL statement.                       |
| `REDIS_URL`      | `redis://127.0.0.1:6379/0` | Redis URL; use `rediss://` for TLS.     |
| `TEST_REDIS_URL` | *(required for tests)* | Separate Redis database/namespace for tests. |
| `REDIS_PASSWORD` / `REDIS_PORT` | *(compose values)* | Redis container password and host port. |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS` / `REDIS_SOCKET_TIMEOUT_SECONDS` | `1` / `1` | Redis connection/command timeouts. |
| `REDIS_HEALTH_CHECK_INTERVAL_SECONDS` | `30` | Idle pooled-connection health check. |
| `REDIS_KEY_PREFIX` | `messenger`          | Namespace for every Redis key.                 |
| `CACHE_ENABLED`  | `true`                | Enable the disposable public-profile cache.    |
| `PROFILE_CACHE_TTL_SECONDS` | `60`       | Public-profile cache lifetime.                 |
| `RATE_LIMIT_FAILURE_POLICY` | `closed` | `closed` = 503 on Redis outage; `open` = allow. |
| `JWT_SECRET_KEY` | *(required)*          | HMAC signing key. **No default** — the app will not start without it. |
| `JWT_ALGORITHM`  | `HS256`               | `HS256` / `HS384` / `HS512` only.              |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15`     | Access-token lifetime (keep short).            |
| `JWT_ISSUER`     | `messenger-api`       | `iss` claim.                                   |
| `JWT_AUDIENCE`   | `messenger-api`       | `aud` claim.                                   |
| `RATE_LIMIT_ENABLED` | `true`            | Enable shared Redis-backed throttling.         |
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
| `MEDIA_STORAGE_BACKEND` | `local`          | `local` or `minio`. Compose sets `minio`. |
| `MEDIA_STORAGE_PATH` | `var/media`         | Local storage root, outside the app package. |
| `MEDIA_MAX_UPLOAD_BYTES` | `10485760`      | Per-file upload ceiling (10 MiB). |
| `MEDIA_MAX_FILES_PER_REQUEST` | `4`         | Files accepted in one multipart upload. |
| `MEDIA_MAX_IMAGE_DIMENSION` | `20000`       | Maximum image width/height read from the header. |
| `MEDIA_RATE_LIMIT_ATTEMPTS` / `_WINDOW_SECONDS` | `60` / `60` | Media budget per account. |
| `MINIO_ENDPOINT` / `MINIO_PUBLIC_ENDPOINT` | `127.0.0.1:9000` | Internal API endpoint and client-facing URL endpoint (host:port, no scheme). |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | *(required for MinIO)* | Least-privilege application credentials; never the root account. |
| `MINIO_BUCKET` | `messenger-media` | Private bucket for media objects. |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | *(compose only)* | MinIO server/initializer credentials; never given to the API. |
| `MINIO_SECURE` / `MINIO_REGION` | `false` / `us-east-1` | TLS and S3 region for the client. |
| `MINIO_KEY_PREFIX` | *(empty)* | Optional application/environment object prefix (trailing slash added). |
| `MINIO_PRESIGNED_URL_EXPIRES_SECONDS` | `300` | Short-lived direct URL lifetime (30–3600). |
| `MINIO_AUTO_CREATE_BUCKET` | `false` | Let startup create a missing bucket; Compose uses `minio-init` instead. |
| `MINIO_CONNECT_TIMEOUT_SECONDS` / `MINIO_READ_TIMEOUT_SECONDS` / `MINIO_MAX_RETRIES` | `2` / `10` / `2` | MinIO client timeouts and transient retries. |
| `MINIO_API_PORT` / `MINIO_CONSOLE_PORT` | `9000` / `9001` | Development host ports (loopback only). |
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

`compose.yaml` also reads `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_PORT`, `REDIS_PASSWORD`, `REDIS_PORT`,
`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ACCESS_KEY`,
`MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_PUBLIC_ENDPOINT`, `MINIO_API_PORT`,
`MINIO_CONSOLE_PORT`, `BACKEND_PORT` and `UVICORN_WORKERS` from `.env`; several
are consumed by Docker Compose and the MinIO initializer rather than directly by
the API.
`TEST_DATABASE_URL` and `TEST_REDIS_URL` are test-only environment variables and
are not part of `Settings`.

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

Channels/broadcast lists and E2EE are planned for later stages, and realtime
fan-out needs a shared pub/sub store to run in more than one process. MinIO
object storage and the containerized API are now implemented; PostgreSQL
operational hardening (managed backups, CI migration checks), off-host object
replication and TLS termination remain deployment work. Rate limiting and the
public-profile cache already use Redis; the token denylist and WebSocket
connection registry still need a shared-store design for multi-process fan-out.

### Known limitations in this phase

* **PostgreSQL is a single primary database, not a cluster.** The Compose
  service is a single PostgreSQL 16 container with one local volume and a
  healthcheck. There is no replication, failover, managed backup, connection
  pooler or multi-node deployment recipe yet. The API is containerized, but the
  development Compose stack is still a single host.
* **The PostgreSQL schema starts empty.** There is no automatic import from the
  former local file database; this phase removed that file because its data was
  not needed. For a real deployment, take and verify backups before any schema
  change.
* **Rate limiting depends on Redis.** Counters are shared and atomic across
  workers, but a Redis outage follows `RATE_LIMIT_FAILURE_POLICY` (503 by
  default, or fail-open when explicitly configured). Redis itself is a single
  development container with no replication or failover recipe yet.
* **The denylist adds one query per authenticated request** (`token_blocklist`),
  and a session lookup adds another. Both are indexed and cheap, but they are
  not free; caching the denylist is still a follow-up. The public-profile cache
  is bounded and invalidated, but a Redis outage simply falls back to PostgreSQL.
* **`request.client.host` is the proxy address** when the API runs behind one,
  which affects the stored session IP and the IP-keyed rate limits. Configure the
  proxy to overwrite `X-Forwarded-For` and read it explicitly before production.
* **Logout revokes only the presented token**, not the other token for the same
  device; use `POST /auth/logout/all` to end every session.
* **Email verification does not gate login** in this phase, and password reset
  may take slightly longer for an unknown address than a known one (the work
  differs), which is a mild timing signal; it is throttled instead.
* **Avatars are an opaque key only** — no upload, resize or serving endpoint yet.
* **Realtime fan-out is single-process.** The WebSocket registry and the
  unauthenticated-socket counters live in one process; with several workers a
  client only receives events produced by the worker it is connected to, and the
  pending caps multiply per worker. A shared pub/sub layer is the follow-up (see
  the realtime section); rate-limit counters are already shared through Redis.
* **WebSocket revocation is bounded, not instantaneous.** An open socket checks
  its token every `WS_TOKEN_RECHECK_INTERVAL_SECONDS` (default 30 s) and on every
  chat message. Logout, `token_version`, expiry and deactivation therefore take
  effect within about one interval; they are not pushed by the database.
* **The per-peer pending cap depends on the proxy.** It keys on
  `request.client.host`; behind a proxy that does not preserve the client
  address, all sockets share the proxy's key.
* **Delivery/read semantics are simple and per account, not per device.**
  In a direct chat `delivered_at` means "pushed to a live socket of the
  recipient, or later read"; groups store one `message_receipts` row per member.
  Neither has a per-device receipt or a push of missed messages on reconnect (the
  client fetches history and marks read). "Read" is a watermark the client
  chooses (`last_message_id`), not a per-message acknowledgement. Typing
  indicators are in-process and ephemeral. Message edits and deletions are still
  out of scope; media attachments are supported and described above.
* **Media storage is a single MinIO node in development.** `MinioStorage` uses
  one private bucket and a least-privilege application user. There is no
  replication, erasure coding, lifecycle policy, object-lock or managed backup
  recipe in `compose.yaml`; production should use a replicated MinIO/S3
  deployment and back up the bucket. The database/object write is not a
  distributed transaction: a crash can still leave an orphan, which the
  conservative `app.tools.media_orphans` reconciler reports and can remove after
  a grace period. The local fallback remains single-host by definition.
* **Presigned URLs are bearer capabilities.** They are issued only after
  authorization and kept short-lived, but anyone who obtains the URL can use it
  until expiry. The API never logs them, and `/content` remains available when a
  deployment does not want direct object URLs.
* **Compose is a single-host development stack.** It provides health checks,
  restart policies and named volumes, not a production orchestrator. TLS,
  reverse-proxy configuration, secret injection from a vault, backup/restore,
  log shipping and rolling deployments remain deployment responsibilities. The
  `migrate` service is a one-shot tool, not an automatic startup migration.
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
* **Groups are not channels.** There is no broadcast-only mode, subscriber list,
  public posting feed, group avatar, group deletion endpoint, invitation expiry or
  automatic ownership election. A group always has exactly one owner; if the
  owner account is disabled, an admin cannot take over without the owner
  transferring first (an operator would need to repair the row).
* **The denormalised `conversations.owner_id` has no database FK.** Adding a
  named FK would require rebuilding the parent table while child FKs exist. The
  `owner` membership role is the authorization source of
  truth; the service keeps `owner_id` in sync on creation and transfer. There is
  no account-deletion endpoint that could orphan it in this phase.
* **Group read counts include receipts from former members.** A member who read a
  message before leaving keeps their receipt, so the historical count remains
  truthful; the member count is current, so counts can briefly exceed the number
  of current recipients. Receipts are not deleted on leave/removal.
* **Blocks do not remove existing group members.** A block prevents invitations
  and public-group joins involving the blocker, but an existing multi-party group
  keeps its members and message flow. Creating a block is not a group
  administration action.
* **Group and public search paging use `limit`/`offset`.** Ordering is stable by
  activity and id, but pages can shift while new messages arrive; a cursor is the
  natural upgrade. Search is substring matching on name/description only (no
  fuzzy ranking, member search or private-group discovery).
* **Blocked participants can still read their old direct history.** The block
  stops new direct messages in both directions, but it does not retroactively
  hide messages the other party already received. The same is true for group
  history that was visible while the account was a member.

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

