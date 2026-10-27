# syntax=docker/dockerfile:1

# Build dependencies into an isolated prefix. The runtime image contains only
# the installed packages, application code and Alembic migrations.
FROM python:3.11-slim-bookworm@sha256:238246c743e70066fe82447003275163b7fe214b7ab85db0cde49ae7076b731d AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /build
COPY requirements.txt .
RUN python -m pip install --prefix=/install -r requirements.txt


FROM python:3.11-slim-bookworm@sha256:238246c743e70066fe82447003275163b7fe214b7ab85db0cde49ae7076b731d AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# ca-certificates is needed for outbound HTTPS (SMTP/object storage). The
# application runs as an unprivileged, no-login user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app \
       --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# Local-storage fallback and the console email outbox are writable by the
# application user. MinIO is used by the Compose stack, so these directories are
# normally empty; named volumes keep the fallback persistent if selected.
RUN mkdir -p /app/var/media /app/var/outbox \
    && chown -R app:app /app/var

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=3)"

# A single worker is the safe default because the WebSocket registry is
# in-process. Set UVICORN_WORKERS explicitly only after reading the documented
# multi-worker limitation.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-1}"]
