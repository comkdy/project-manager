"""Register only the managed local demo; analysis DB URLs do not belong in .env."""
import json
from pathlib import Path
import sys

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def register_demo():
    from app.bootstrap import initialize
    from app.db import SessionLocal, SourceBinding, database_key, make_engine, settings, use_source
    from app.database_settings import profile_from_url
    from app.models import DatabaseMount, DatabaseProfile, DatabaseProfileRemoval
    from app.query import source_connection

    runtime = json.loads((ROOT / ".local" / "runtime.json").read_text(encoding="utf-8-sig"))
    target = make_url(settings.app_database_url)
    if (not settings.demo_mode or runtime.get("marker") != "wafer-local-demo-v1"
            or target.host != "127.0.0.1" or target.port != runtime["port"]
            or target.database != "wafer_app" or target.username != "wafer_metadata"):
        raise ValueError("Demo registration requires this workspace's managed local metadata DB.")
    url = URL.create("postgresql+psycopg", username="wafer_source", password=runtime["source_password"],
                     host="127.0.0.1", port=runtime["port"], database="wafer_demo")
    engine, key = make_engine(url, read_only=True), database_key(url)
    try:
        with use_source(SourceBinding(engine, key, ignored_tables=())):
            initialize()
            with SessionLocal.begin() as db:
                db.execute(text("SELECT pg_advisory_xact_lock(7126002004)"))
                if db.get(DatabaseProfile, key) or db.get(DatabaseProfileRemoval, key):
                    print("Existing demo registration/visibility and active DB selection preserved.")
                    return
                with source_connection() as connection:
                    if connection.scalar(text("SELECT marker FROM public.demo_identity")) != "wafer-local-demo-v1":
                        raise ValueError("The database is not the managed demo.")
                db.add(profile_from_url(url))
                db.flush()
                if db.get(DatabaseMount, 1) is None:
                    db.add(DatabaseMount(id=1, profile_id=key, revision=1))
                print("Demo source saved in web DB settings; no SOURCE_DATABASE_URL is required.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        register_demo()
    except Exception:
        # Never print runtime passwords or database connection exceptions.
        print("Demo registration failed. Check the managed runtime and metadata DB configuration.", file=sys.stderr)
        sys.exit(1)
