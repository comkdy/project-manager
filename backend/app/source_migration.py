"""Bind existing definitions to their current source database once."""
from sqlalchemy import text

from .db import source_database_key


def migrate_source_connections(engine):
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(7126002002)"))
        connection.execute(text("ALTER TABLE app_sources ADD COLUMN IF NOT EXISTS connection_key VARCHAR(64)"))
        connection.execute(text("ALTER TABLE app_sources ADD COLUMN IF NOT EXISTS available BOOLEAN NOT NULL DEFAULT TRUE"))
        connection.execute(text("UPDATE app_sources SET connection_key=:key WHERE connection_key IS NULL"),
                           {"key": source_database_key()})
        connection.execute(text("ALTER TABLE app_dashboards ADD COLUMN IF NOT EXISTS connection_key VARCHAR(64)"))
        connection.execute(text("UPDATE app_dashboards SET connection_key=:key WHERE connection_key IS NULL"),
                           {"key": source_database_key()})
