"""Alembic environment.

The database URL is taken from, in order of precedence:

1. ``-x db_url=...`` on the command line (used by the test-suite),
2. ``sqlalchemy.url`` in ``alembic.ini`` (or a ``Config`` built in code),
3. the application settings (``DATABASE_URL``).

Importing ``app.db.models`` is what populates ``Base.metadata`` for
``--autogenerate``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_database_settings
from app.db.base import Base

# Importing the models package registers every table on Base.metadata.
import app.db.models  # noqa: F401  (side-effect import)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """Determine which database URL Alembic should use."""
    x_args = context.get_x_argument(as_dictionary=True)
    if url := x_args.get("db_url"):
        return url
    if url := config.get_main_option("sqlalchemy.url"):
        return url
    # Only needs the database settings, so migrations do not require the
    # application's JWT secret to be present.
    return get_database_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live connection)."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER most things; batch mode rewrites tables safely.
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
