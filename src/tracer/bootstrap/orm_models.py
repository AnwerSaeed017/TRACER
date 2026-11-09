"""Every table model, gathered so Alembic can compare them with the database.

Each module's persistence models register themselves on ``Base.metadata`` when imported.
When a module gains tables, import its models module here; autogenerate will then see them.
"""

import tracer.modules.ingestion.adapters.persistence.models
import tracer.modules.supply_chain.adapters.persistence.models
import tracer.platform.messaging.models  # noqa: F401
from tracer.platform.database.base import Base

metadata = Base.metadata
