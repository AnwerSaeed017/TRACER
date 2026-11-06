"""Every table model, gathered so Alembic can compare them with the database.

Each module's persistence models register themselves on ``Base.metadata`` when imported.
When a module gains tables, import its models module here; autogenerate will then see them.
"""

from tracer.platform.database.base import Base

metadata = Base.metadata
