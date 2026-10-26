"""Real PostgreSQL races: one board and one ordered dataset version stream."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
import uuid

from sqlalchemy import delete, select

from app import db as database, main, source_discovery
from app.db import SessionLocal
from app.models import Audit, Dashboard, DatasetVersion, Source
from app.schemas import DatasetUpdate
from test_project_datasets import api


def test_two_first_visitors_receive_the_same_dashboard(api, monkeypatch):
    key = uuid.uuid4().hex * 2
    binding = database.SourceBinding(None, key)
    ready = Barrier(2)

    def initial(project_id):
        ready.wait(timeout=5)
        return Dashboard(id="unused", project_id=None, version=1, cards=[], layout=[], connection_key=key)

    monkeypatch.setattr(main, "initial_dashboard", initial)

    def visit():
        with database.use_source(binding):
            return main.get_dashboard(None, SimpleNamespace(id="qa-concurrent-visitor"))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(visit), pool.submit(visit)
            a, b = first.result(timeout=10), second.result(timeout=10)
        assert a == b
        assert a["version"] == 1 and a["cards"] == []
        with SessionLocal() as db:
            assert len(list(db.scalars(select(Dashboard).where(Dashboard.connection_key == key)))) == 1
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Dashboard).where(Dashboard.connection_key == key))


def test_discovery_and_manual_save_share_the_same_dataset_version_lock(api, monkeypatch):
    key = uuid.uuid4().hex * 2
    binding = database.SourceBinding(None, key)
    metadata = [{"schema_name": "factory", "table_name": "samples", "primary_key": "id",
                 "columns": [{"name": "id", "type": "text"}, {"name": "value", "type": "text"}]}]
    monkeypatch.setattr(source_discovery, "database_tables", lambda: metadata)
    preview_entered, release_preview, discovery_entered, discovery_acquired = Event(), Event(), Event(), Event()
    real_lock = source_discovery.lock_dataset_version

    def observed_lock(db, dataset_id):
        discovery_entered.set()
        real_lock(db, dataset_id)
        discovery_acquired.set()

    try:
        with database.use_source(binding), SessionLocal.begin() as db:
            source = source_discovery.refresh_sources(db)[0]
            dataset_id = source["id"]
            existing = db.get(DatasetVersion, (dataset_id, 1))
            columns = list(existing.columns)
            authored_spec = dict(existing.spec, raw_sql=existing.spec["raw_sql"] + "\nWHERE value IS NOT NULL")

        def preview(spec, context):
            preview_entered.set()
            assert release_preview.wait(10), "Test did not release the manual save"
            return {"spec": authored_spec, "columns": columns}

        monkeypatch.setattr(main, "preview_dataset", preview)
        monkeypatch.setattr(source_discovery, "lock_dataset_version", observed_lock)

        def save():
            with database.use_source(binding):
                return main.dataset_version(dataset_id, DatasetUpdate(name="My authored analysis",
                    spec=authored_spec, project_id=None, expected_version=1), SimpleNamespace(id="qa-version-race"))

        def discover():
            with database.use_source(binding), SessionLocal.begin() as db:
                return source_discovery.refresh_sources(db)

        with ThreadPoolExecutor(max_workers=2) as pool:
            saving = pool.submit(save)
            try:
                assert preview_entered.wait(5)
                metadata[0] = dict(metadata[0], columns=columns + [{"name": "added_column", "type": "text"}])
                discovering = pool.submit(discover)
                assert discovery_entered.wait(5)
                assert not discovery_acquired.wait(0.2), "Discovery bypassed the in-flight version writer"
            finally:
                release_preview.set()
            assert saving.result(timeout=10)["version"] == 2
            discovering.result(timeout=10)
        with SessionLocal() as db:
            versions = list(db.scalars(select(DatasetVersion).where(
                DatasetVersion.id == dataset_id).order_by(DatasetVersion.version)))
            assert [d.version for d in versions] == [1, 2]
            assert versions[-1].name == "My authored analysis"
            assert versions[-1].spec == authored_spec
            assert len(db.get(Source, dataset_id).columns) == 3
    finally:
        release_preview.set()
        with SessionLocal.begin() as db:
            ids = list(db.scalars(select(Source.id).where(Source.connection_key == key)))
            db.execute(delete(DatasetVersion).where(DatasetVersion.id.in_(ids)))
            db.execute(delete(Source).where(Source.connection_key == key))
            db.execute(delete(Audit).where(Audit.user_id == "qa-version-race"))
