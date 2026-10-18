# Messenger — Backend API

Backend for the messenger project, built with **Python 3.11** and **FastAPI**.

> Stage 1 deliverable: a clean, extensible backend skeleton with a health
> endpoint, environment-based configuration and an automated test suite.
> No database, authentication or messaging yet.

## Project layout

```
messenger/
├── app/
│   ├── __init__.py
│   ├── main.py                  # create_app() factory + module-level `app`
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # pydantic-settings configuration
│   │   └── errors.py            # AppError hierarchy + exception handlers
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py        # aggregates all v1 routers
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           └── health.py    # GET /api/v1/health
│   └── schemas/
│       ├── __init__.py
│       └── health.py            # response models
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # app / client / settings fixtures
│   ├── test_app.py              # startup, docs, wiring
│   └── test_health.py           # health endpoint
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
```

Create a local environment file:

```powershell
Copy-Item .env.example .env
```

## Run the server

```powershell
uvicorn app.main:app --reload
```

| URL                                        | Description                 |
| ------------------------------------------ | --------------------------- |
| http://127.0.0.1:8000/docs                 | Swagger UI                  |
| http://127.0.0.1:8000/redoc                | ReDoc                       |
| http://127.0.0.1:8000/openapi.json         | OpenAPI schema              |
| http://127.0.0.1:8000/api/v1/health        | Health check                |

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

## Configuration

Settings are managed with `pydantic-settings` and read from environment
variables or the local `.env` file. See `.env.example` for every supported key:

| Variable         | Default      | Description                                |
| ---------------- | ------------ | ------------------------------------------ |
| `APP_NAME`       | `Messenger API` | Service name shown in the docs.         |
| `APP_ENV`        | `development` | Deployment environment.                   |
| `APP_DEBUG`      | `false`      | Enable debug behaviour.                    |
| `APP_VERSION`    | `0.1.0`      | API version exposed in `/openapi.json`.    |
| `API_V1_PREFIX`  | `/api/v1`    | Prefix for version 1 routes.               |
| `HOST` / `PORT`  | `127.0.0.1` / `8000` | Suggested bind address.           |
| `CORS_ORIGINS`   | *(empty)*    | Comma-separated allowed origins.           |

## Error responses

Unexpected failures are normalised to a consistent JSON envelope:

```json
{ "error": { "code": "internal_error", "message": "An unexpected error occurred." } }
```

## Roadmap

Later stages will add authentication, persistence (database), realtime
messaging and object storage. Stage 1 intentionally ships none of these.
