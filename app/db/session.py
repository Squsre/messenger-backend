"""Database engine, session factory and the FastAPI session dependency.

The :class:`Database` wrapper owns an :class:`~sqlalchemy.engine.Engine` and its
session factory. It provides a context manager that guarantees sessions are
always closed (and rolled back on error), plus a FastAPI dependency that yields
a request-scoped session.

Usage in endpoints::

    from sqlalchemy.orm import Session
    from fastapi import Depends
    from app.db.session import get_db

    def my_endpoint(db: Session = Depends(get_db)) -> None:
        ...
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    """Build a PostgreSQL engine for the given URL.

    PostgreSQL is the only supported backend. ``pool_pre_ping`` protects pooled
    connections from stale sockets after database restarts or network errors.
    """
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError(
            "Only PostgreSQL URLs using the psycopg driver are supported "
            "(postgresql+psycopg://...)."
        )

    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        future=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Database:
    """Owns an engine and its session factory."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.engine = create_db_engine(url, echo=echo)
        self.session_factory = create_session_factory(self.engine)

    @property
    def safe_url(self) -> str:
        """The database URL with any password redacted, safe for logs.

        ``DATABASE_URL`` may contain a PostgreSQL password. Startup logs and
        diagnostics must never print it verbatim.
        """
        return make_url(self.url).render_as_string(hide_password=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session, committing on success and rolling back on error.

        The session is always closed, so connections are returned to the pool
        even when an exception is raised.
        """
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Dispose of the connection pool. Call this on application shutdown."""
        self.engine.dispose()


@lru_cache
def get_database() -> Database:
    """Return the process-wide :class:`Database` built from application settings."""
    settings: Settings = get_settings()
    return Database(settings.database_url, echo=settings.db_echo)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    The transaction is committed when the request completes successfully and
    rolled back if a handler raises, so endpoints only need to add/flush their
    changes. The session is always closed afterwards.

    The database instance attached to ``app.state`` is preferred so that tests
    (and workers) can substitute their own isolated database.
    """
    database: Database = getattr(request.app.state, "database", None) or get_database()
    session = database.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
