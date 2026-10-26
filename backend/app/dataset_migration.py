"""Add project-owned query definitions without changing existing shared datasets."""
from sqlalchemy import text


def migrate_dataset_scope(engine):
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(7126002001)"))
        connection.execute(text("ALTER TABLE app_dataset_versions ADD COLUMN IF NOT EXISTS project_id INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_app_dataset_versions_project_id ON app_dataset_versions(project_id)"))
