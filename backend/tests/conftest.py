"""Keep legacy demo tests on their explicit environment DB, not a UI preset."""
import pytest
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url


def configure_local_fixture_source():
    """Legacy query tests use the managed demo, never an arbitrary UI-mounted DB."""
    root = Path(__file__).resolve().parents[2]
    env = {**dotenv_values(root / ".env"), **os.environ}
    runtime_file = root / ".local" / "runtime.json"
    if env.get("SOURCE_DATABASE_URL") or str(env.get("DEMO_MODE", "")).lower() != "true" or not runtime_file.exists():
        return
    runtime = json.loads(runtime_file.read_text(encoding="utf-8-sig"))
    metadata = make_url(env.get("APP_DATABASE_URL", ""))
    if (runtime.get("marker") != "wafer-local-demo-v1" or metadata.host != "127.0.0.1"
            or metadata.port != runtime["port"] or metadata.database != "wafer_app"
            or metadata.username != "wafer_metadata"):
        return
    os.environ["SOURCE_DATABASE_URL"] = URL.create("postgresql+psycopg", username="wafer_source",
        password=runtime["source_password"], host="127.0.0.1", port=runtime["port"],
        database="wafer_demo").render_as_string(hide_password=False)


configure_local_fixture_source()


@pytest.fixture(autouse=True)
def isolate_demo_source(request, monkeypatch):
    from app import db as database
    # Connection-management tests exercise the real persisted selection and
    # restore it themselves. Other tests historically patch the demo env.
    if not database.settings.demo_mode or request.module.__name__.endswith("test_database_settings"):
        return
    from app import main
    monkeypatch.setattr(database, "active_source", None)
    monkeypatch.setattr(main, "load_active_source", lambda: database.SourceBinding(
        database.source_engine, database.database_key(database.settings.source_database_url)))
