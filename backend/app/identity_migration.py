"""Add identity fields without deleting legacy users, sessions, or owned data."""
from sqlalchemy import text


def migrate_identity(engine):
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(7126001901)"))
        for clause in (
            "employee_id VARCHAR(64)",
            "name VARCHAR(120) NOT NULL DEFAULT ''",
            "team VARCHAR(200) NOT NULL DEFAULT ''",
            "active BOOLEAN NOT NULL DEFAULT TRUE",
            "profile_source VARCHAR(20) NOT NULL DEFAULT 'legacy'",
            "profile_synced_at TIMESTAMPTZ",
            "version INTEGER NOT NULL DEFAULT 1",
        ):
            connection.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS " + clause))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS app_users_employee_id_idx ON app_users(employee_id)"))
