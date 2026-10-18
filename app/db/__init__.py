"""Database infrastructure: engine, sessions and the declarative base.

This package is intentionally free of feature-specific logic. Import the
objects you need from their submodules::

    from app.db.base import Base
    from app.db.session import get_db
"""

from app.db.base import Base

__all__ = ["Base"]
