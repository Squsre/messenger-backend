"""ORM models.

Importing this package registers every model on ``Base.metadata``, which is
what Alembic (and ``Base.metadata.create_all``) relies on. Add new models by
creating a module in this package and re-exporting it here.
"""

from app.db.models.health_check import HealthCheck

__all__ = ["HealthCheck"]
