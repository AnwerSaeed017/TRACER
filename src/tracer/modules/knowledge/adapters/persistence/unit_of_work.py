from tracer.modules.knowledge.adapters.persistence.repository import SqlAlchemyOntologyRepository
from tracer.platform.database.unit_of_work import SqlAlchemyUnitOfWork


class SqlAlchemyOntologyUnitOfWork(SqlAlchemyUnitOfWork):
    @property
    def ontology(self) -> SqlAlchemyOntologyRepository:
        return SqlAlchemyOntologyRepository(self.session)
