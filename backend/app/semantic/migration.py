from sqlalchemy import inspect, select, text

from .models import SemanticBase, SemanticMigration
from . import collector_models  # Register additive collection tables on the same metadata.
from ..models import utcnow


def migrate_semantic(engine):
    """Add only semantic tables; never seed business rules or touch source data."""
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(7126002020)"))
        SemanticBase.metadata.create_all(connection)
        if 'execution_revision' not in {c['name'] for c in inspect(connection).get_columns('app_semantic_collection_settings')}:
            connection.execute(text('ALTER TABLE app_semantic_collection_settings ADD COLUMN execution_revision INTEGER NOT NULL DEFAULT 1'))
        if "context_source_ids" not in {c["name"] for c in inspect(connection).get_columns("app_semantic_questions")}:
            connection.execute(text("ALTER TABLE app_semantic_questions ADD COLUMN context_source_ids JSON NOT NULL DEFAULT '[]'"))
        for table in ("app_semantic_questions", "app_semantic_answers"):
            if "targets" not in {c["name"] for c in inspect(connection).get_columns(table)}:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN targets JSON NOT NULL DEFAULT '[]'"))
        if connection.scalar(select(SemanticMigration.version).where(SemanticMigration.version == 1)) is None:
            connection.execute(SemanticMigration.__table__.insert().values(version=1, applied_at=utcnow()))
        if connection.scalar(select(SemanticMigration.version).where(SemanticMigration.version == 2)) is None:
            connection.execute(SemanticMigration.__table__.insert().values(version=2, applied_at=utcnow()))
        if connection.scalar(select(SemanticMigration.version).where(SemanticMigration.version == 3)) is None:
            connection.execute(SemanticMigration.__table__.insert().values(version=3, applied_at=utcnow()))
        if connection.scalar(select(SemanticMigration.version).where(SemanticMigration.version == 4)) is None:
            connection.execute(SemanticMigration.__table__.insert().values(version=4, applied_at=utcnow()))
        if connection.scalar(select(SemanticMigration.version).where(SemanticMigration.version == 5)) is None:
            connection.execute(SemanticMigration.__table__.insert().values(version=5, applied_at=utcnow()))
