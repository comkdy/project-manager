import uuid

import pytest
from sqlalchemy import delete, event, select

from app import source_discovery
from app.db import SessionLocal, settings, source_database_key, source_engine
from app.models import Dashboard, DatasetVersion, Source
from app.table_visibility import TableVisibility
from test_project_datasets import api


def table(schema, name, columns=("id", "value"), primary_key="id"):
    return {"schema_name": schema, "table_name": name, "primary_key": primary_key,
            "columns": [{"name": c, "type": "text"} for c in columns]}


@pytest.fixture
def other_database(api, monkeypatch):
    database = "qa_discovery_" + uuid.uuid4().hex[:12]
    with monkeypatch.context() as patched:
        patched.setattr(settings, "source_database_url", "postgresql://reader:unused@other-host/" + database)
        key = source_database_key()
        yield patched
        with SessionLocal.begin() as db:
            ids = list(db.scalars(select(Source.id).where(Source.connection_key == key)))
            db.execute(delete(DatasetVersion).where(DatasetVersion.id.in_(ids)))
            db.execute(delete(Source).where(Source.connection_key == key))
            db.execute(delete(Dashboard).where(Dashboard.connection_key == key))


def test_new_database_loads_exact_names_columns_and_initial_datasets(api, other_database):
    metadata = [table("quality", "Measurements", ("Batch ID", "yield_pct"), ""),
                table("history", "Measurements", ("batch", "reading"), "")]
    other_database.setattr(source_discovery, "database_tables", lambda: metadata)
    response = api.get("/api/bootstrap")
    assert response.status_code == 200, response.text
    body = response.json()
    assert {(s["schema_name"], s["table_name"]) for s in body["catalog"]} == {
        ("quality", "Measurements"), ("history", "Measurements")}
    assert len({s["id"] for s in body["catalog"]}) == 2
    assert {d["id"] for d in body["datasets"]} == {s["id"] for s in body["catalog"]}
    source = next(s for s in body["catalog"] if s["schema_name"] == "quality")
    assert [c["name"] for c in source["columns"]] == ["Batch ID", "yield_pct"]
    assert source["name"] == "Measurements"
    assert 'quality."Measurements"' in source["default_sql"]
    assert '"Batch ID"' in source["default_sql"]
    assert body["projects"] == []
    assert api.post("/api/query", json={"dataset_id": "equipment", "dataset_version": 1}).status_code == 404
    assert api.get("/api/datasets/equipment/versions").status_code == 404
    dashboard = api.get("/api/dashboards").json()
    assert not dashboard["cards"] and dashboard["id"] != "common"
    assert api.put("/api/dashboards/common", json={"version": 1, "cards": [], "layout": []}).status_code == 404
    with SessionLocal() as db:
        assert db.get(DatasetVersion, ("equipment", 1)) is not None
        policy = TableVisibility.load(db)
        assert policy.allows_spec({"source_id": source["id"], "raw_sql": source["default_sql"]})


def test_schema_changes_refresh_auto_dataset_and_preserve_old_version(api, other_database):
    metadata = [table("factory", "samples", ("lot", "value"), "")]
    other_database.setattr(source_discovery, "database_tables", lambda: metadata)
    first = api.get("/api/bootstrap").json()
    id = first["catalog"][0]["id"]
    metadata[0] = table("factory", "samples", ("lot", "value", "measured_at"), "")
    updated = api.get("/api/bootstrap").json()
    dataset = updated["datasets"][0]
    assert dataset["version"] == 2
    assert [c["name"] for c in updated["catalog"][0]["columns"]] == ["lot", "value", "measured_at"]
    assert [c["name"] for c in dataset["columns"]] == ["lot", "value", "measured_at"]
    versions = api.get(f"/api/datasets/{id}/versions").json()
    assert len(versions) == 2 and len(versions[1]["columns"]) == 2
    assert api.get("/api/bootstrap").json()["datasets"][0]["version"] == 2
    with SessionLocal.begin() as db:
        latest = db.get(DatasetVersion, (id, 2))
        db.add(DatasetVersion(id=id, version=3, name="My analysis", columns=latest.columns,
                              spec=dict(latest.spec, raw_sql=latest.spec["raw_sql"] + "\nWHERE value IS NOT NULL")))
    metadata[0] = table("factory", "samples", ("lot", "value", "measured_at", "status"), "")
    authored = api.get("/api/bootstrap").json()
    assert authored["datasets"][0]["version"] == 3
    assert authored["datasets"][0]["name"] == "My analysis"
    assert len(authored["catalog"][0]["columns"]) == 4
    # A removed table disappears; restoring it reuses its definitions.
    metadata.clear()
    assert api.get("/api/bootstrap").json()["datasets"] == []
    assert api.post("/api/query", json={"dataset_id": id, "dataset_version": 2}).status_code == 404
    metadata.append(table("factory", "samples", ("lot", "value", "measured_at"), ""))
    assert api.get("/api/bootstrap").json()["datasets"][0]["id"] == id


def test_old_generated_quotes_get_a_new_version_without_rewriting_history(api, other_database):
    other_database.setattr(source_discovery, "database_tables", lambda: [table("factory", "samples")])
    source = api.get("/api/bootstrap").json()["catalog"][0]
    old_sql = source_discovery.default_sql(source, quote_all=True)
    with SessionLocal.begin() as db:
        old = db.get(DatasetVersion, (source["id"], 1))
        old.spec = dict(old.spec, raw_sql=old_sql)
    updated = api.get("/api/bootstrap").json()["datasets"][0]
    assert updated["version"] == 2 and '"' not in updated["spec"]["raw_sql"]
    with SessionLocal() as db:
        assert db.get(DatasetVersion, (source["id"], 1)).spec["raw_sql"] == old_sql
    assert api.get("/api/bootstrap").json()["datasets"][0]["version"] == 2


def test_ignore_applies_before_automatic_connection(api, other_database):
    other_database.setattr(settings, "table_ignore", ["private.hidden", "audit"])
    other_database.setattr(source_discovery, "database_tables", lambda: [
        table("private", "hidden"), table("public", "audit"), table("factory", "samples")])
    result = api.post("/api/sources/discover", json={})
    assert result.status_code == 200, result.text
    assert [s["table_name"] for s in result.json()] == ["samples"]


def test_real_database_discovery_and_keyless_preview_are_read_only(api, monkeypatch):
    metadata = source_discovery.database_tables()
    actual = next(t for t in metadata if t["table_name"] == "demo_identity")
    assert actual["primary_key"] == "marker"
    # A keyless source follows the same SQL path; no guessed "id" is needed.
    keyless = dict(actual, primary_key="")
    metadata = [keyless if t is actual else t for t in metadata]
    sent = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        sent.append(statement)
    event.listen(source_engine, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patched:
            patched.setattr(source_discovery, "database_tables", lambda: metadata)
            response = api.post("/api/sources/discover", json={})
            assert response.status_code == 200, response.text
            source = next(s for s in response.json() if s["table_name"] == "demo_identity")
            result = api.post("/api/datasets/preview", json={
                "spec": {"source_id": source["id"]}, "editor_mode": "simple"})
            assert result.status_code == 200, result.text
            assert result.json()["rows"] == [{"marker": "wafer-local-demo-v1"}]
            stored = api.post("/api/query", json={"dataset_id": source["id"], "dataset_version": 1})
            assert stored.status_code == 200, stored.text
            legacy = api.post("/api/datasets/preview", json={"spec": {"source_id": source["id"]}})
            assert legacy.status_code == 200, legacy.text
        assert not any(s.lstrip().upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")) for s in sent)
    finally:
        event.remove(source_engine, "before_cursor_execute", capture)
        api.post("/api/sources/discover", json={})


def test_connection_identity_ignores_credentials_but_separates_databases(monkeypatch):
    monkeypatch.setattr(settings, "source_database_url", "postgresql://one:first@host:5432/production")
    original = source_database_key()
    monkeypatch.setattr(settings, "source_database_url", "postgresql://two:second@host/production")
    assert source_database_key() == original
    monkeypatch.setattr(settings, "source_database_url", "postgresql://two:second@host/another")
    assert source_database_key() != original
