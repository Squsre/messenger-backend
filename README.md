# Messenger — Backend API

Backend for the messenger project, built with **Python 3.11**, **FastAPI** and
**SQLAlchemy 2.0** (SQLite by default), with **Alembic** for migrations.

> Delivered so far:
> **Stage 1** — clean FastAPI skeleton with a health endpoint, configuration and tests.
> **Stage 2** — database infrastructure (engine, sessions, base models) and Alembic migrations.
>
> No authentication, users, chat or messaging yet — those come later.

## Project layout

```
messenger/
├── app/
│   ├── __init__.py
│   ├── main.py                  # create_app() factory + module-level `app`
│   ├── core/
│   │   ├── config.py            # pydantic-settings configuration
│   │   ├── dependencies.py      # get_app_settings
│   │   └── errors.py            # AppError hierarchy + exception handlers
│   ├── db/
│   │   ├── base.py              # declarative Base, naming convention, mixins
│   │   ├── session.py           # engine, Database, get_db dependency
│   │   └── models/
│   │       └── health_check.py  # infrastructure model (connectivity checks)
│   ├── api/
│   │   └── v1/
│   │       ├── router.py        # aggregates all v1 routers
│   │       └── endpoints/
│   │           └── health.py    # GET /api/v1/health
│   └── schemas/
│       └── health.py            # response models
├── migrations/                  # Alembic environment
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_create_health_checks.py
├── tests/
│   ├── conftest.py              # app / client / settings / database fixtures
│   ├── test_app.py              # startup, docs, wiring
│   ├── test_health.py           # health endpoint
│   ├── test_database.py         # engine, sessions, get_db dependency
│   └── test_migrations.py       # real `alembic upgrade/downgrade`
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
the ORM models (no drift).

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

## Error responses

Unexpected failures are normalised to a consistent JSON envelope:

```json
{ "error": { "code": "internal_error", "message": "An unexpected error occurred." } }
```

## Roadmap

Authentication, real users, chat/messaging, realtime transport and object
storage are planned for later stages.
