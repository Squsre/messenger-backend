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

import sqlalchemy as sa
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


def _ensure_postgres_version_table(connection: sa.Connection) -> None:
    """Widen Alembic's bookkeeping column before the first migration runs.

    Alembic 1.14 creates ``alembic_version.version_num`` as ``VARCHAR(32)``.
    This project's revision ids are longer (for example
    ``0002_create_users_and_token_blocklist`` is 38 characters), so PostgreSQL
    would reject the version update. Pre-creating the tiny bookkeeping table
    with a wider column is safe: it is not application data and it does not
    rewrite any migration. Existing databases are widened in place.
    """

    with connection.begin():
        inspector = sa.inspect(connection)
        if "alembic_version" not in inspector.get_table_names():
            connection.execute(
                sa.text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(255) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )
            )
            return

        columns = {
            column["name"]: column
            for column in inspector.get_columns("alembic_version")
        }
        version_type = columns.get("version_num", {}).get("type")
        length = getattr(version_type, "length", None)
        if length is not None and length < 255:
            connection.execute(
                sa.text(
                    "ALTER TABLE alembic_version "
                    "ALTER COLUMN version_num TYPE VARCHAR(255)"
                )
            )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
        # Must happen before context.configure() creates the version table.
        _ensure_postgres_version_table(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
