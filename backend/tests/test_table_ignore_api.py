import uuid

import pytest
from sqlalchemy import delete, event

from app.db import SessionLocal, settings, source_engine
from app.models import Dashboard, DatasetVersion, Source
from test_project_datasets import api


def test_ignore_applies_to_discovery_catalog_relations_and_existing_data(api, monkeypatch):
    before = api.get("/api/bootstrap").json()
    assert "equipment" in {s["id"] for s in before["catalog"]}
    with monkeypatch.context() as patched:
        patched.setattr(settings, "table_ignore", ["public.equipment"])
        discovered = api.get("/api/admin/source-tables")
        assert discovered.status_code == 200
        assert not any(t["schema_name"] == "public" and t["table_name"] == "equipment" for t in discovered.json())
        current = api.get("/api/bootstrap").json()
        assert "equipment" not in {s["id"] for s in current["catalog"]}
        assert "equipment" not in {d["id"] for d in current["datasets"]}
        assert "equipment" not in {s["id"] for s in api.get("/api/admin/sources").json()}
        assert all(r["right_source_id"] != "equipment" for r in api.get("/api/relations").json())
        assert api.get("/api/datasets/equipment/versions").status_code == 404
        from app.assistant import ChatRequest, proposal_catalog
        _, sources = proposal_catalog(ChatRequest(message="test", dataset_id="inline_measurements"))
        assert "equipment" not in {s["id"] for s in sources}
        with SessionLocal() as db:
            assert db.get(Source, "equipment") is not None
            assert db.get(DatasetVersion, ("equipment", 1)) is not None
    restored = api.get("/api/bootstrap").json()
    assert {s["id"] for s in restored["catalog"]} == {s["id"] for s in before["catalog"]}
    assert {d["id"] for d in restored["datasets"]} == {d["id"] for d in before["datasets"]}


def test_ignored_tables_cannot_be_registered_or_queried_by_known_id_or_sql(api, monkeypatch):
    monkeypatch.setattr(settings, "table_ignore", ["equipment"])
    sent = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        sent.append(statement)
    event.listen(source_engine, "before_cursor_execute", capture)
    try:
        source = {"id": "qa_ignored", "name": "Ignored", "schema_name": "public",
                  "table_name": "equipment", "primary_key": "id"}
        assert api.post("/api/admin/sources", json=source).status_code == 404
        assert api.post("/api/query", json={"dataset_id": "equipment", "dataset_version": 1}).status_code == 404
        assert api.post("/api/datasets/preview", json={"spec": {"source_id": "equipment"}}).status_code == 404
        assert api.post("/api/datasets/simple-editor", json={"source_id": "equipment", "sql": "SELECT * FROM equipment"}).status_code == 404
        assert api.post("/api/admin/relations", json={
            "id": "qa_ignored", "left_source_id": "inline_measurements", "right_source_id": "equipment",
            "left_column": "equipment_id", "right_column": "id",
        }).status_code == 404
        response = api.post("/api/datasets/preview", json={"spec": {"source_id": "inline_measurements",
            "raw_sql": "SELECT m.id FROM inline_measurements m JOIN equipment e ON m.equipment_id=e.id LIMIT 1"}})
        assert response.status_code == 422, response.text
        assert not sent
    finally:
        event.remove(source_engine, "before_cursor_execute", capture)


def test_saved_sql_dependencies_and_cached_cards_are_hidden_and_restore(api, monkeypatch):
    spec = {"source_id": "inline_measurements", "raw_sql":
        "SELECT m.id, e.name FROM inline_measurements m JOIN equipment e ON m.equipment_id=e.id LIMIT 2"}
    saved = api.post("/api/datasets", json={"name": "Ignore dependency QA " + uuid.uuid4().hex[:8], "spec": spec})
    assert saved.status_code == 201, saved.text
    dataset = saved.json()
    card = {"id": "qa_ignore", "title": "QA", "dataset_id": dataset["id"], "dataset_version": 1,
            "kind": "kpi", "aggregate": "count"}
    try:
        assert api.post("/api/cards/query", json={"card": card, "context": {"refresh": True}}).status_code == 200
        with monkeypatch.context() as patched:
            patched.setattr(settings, "table_ignore", ["equipment"])
            assert dataset["id"] not in {d["id"] for d in api.get("/api/bootstrap").json()["datasets"]}
            assert api.get(f"/api/datasets/{dataset['id']}/versions").status_code == 404
            assert api.post("/api/query", json={"dataset_id": dataset["id"], "dataset_version": 1}).status_code == 404
            assert api.post("/api/cards/query", json={"card": card}).status_code == 404
        response = api.post("/api/query", json={"dataset_id": dataset["id"], "dataset_version": 1})
        assert response.status_code == 200 and len(response.json()["rows"]) == 2
        assert api.get(f"/api/datasets/{dataset['id']}/versions").json()[0]["spec"] == dataset["spec"]
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id == dataset["id"]))


def test_hidden_dashboard_cards_survive_edits_to_visible_cards(api, monkeypatch):
    from app.main import dashboard_json
    id = "qa_ignore_" + uuid.uuid4().hex[:12]
    cards = [{"id": name, "title": name, "dataset_id": dataset, "dataset_version": 1,
              "kind": "kpi", "aggregate": "count"} for name, dataset in [
                  ("hidden", "equipment"), ("visible", "inline_measurements")]]
    layout = [{"i": c["id"], "x": i*3, "y": 0, "w": 3, "h": 3} for i, c in enumerate(cards)]
    with SessionLocal.begin() as db:
        db.add(Dashboard(id=id, version=1, cards=cards, layout=layout))
    try:
        with monkeypatch.context() as patched:
            patched.setattr(settings, "table_ignore", ["equipment"])
            with SessionLocal() as db:
                shown = dashboard_json(db.get(Dashboard, id), db)
                assert [c["id"] for c in shown["cards"]] == ["visible"]
                assert [p["i"] for p in shown["layout"]] == ["visible"]
            shown["cards"][0]["title"] = "Edited"
            response = api.put(f"/api/dashboards/{id}", json={
                "version": 1, "cards": shown["cards"], "layout": shown["layout"],
            })
            assert response.status_code == 200, response.text
            assert [c["id"] for c in response.json()["cards"]] == ["visible"]
        with SessionLocal() as db:
            restored = dashboard_json(db.get(Dashboard, id), db)
            assert {c["id"] for c in restored["cards"]} == {"visible", "hidden"}
            assert {p["i"] for p in restored["layout"]} == {"visible", "hidden"}
            assert next(c for c in restored["cards"] if c["id"] == "hidden")["title"] == "hidden"
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Dashboard).where(Dashboard.id == id))


def test_ignore_does_not_break_demo_startup_or_reregister_excluded_tables(api, monkeypatch):
    import app.bootstrap as bootstrap
    monkeypatch.setattr(settings, "table_ignore", ["equipment", "demo_identity"])
    monkeypatch.setattr(bootstrap, "SOURCE_NAMES", {"demo_identity": "Excluded demo table"})
    def unexpected_registration(*args, **kwargs):
        pytest.fail("Ignored table was registered")
    monkeypatch.setattr(bootstrap, "register_source", unexpected_registration)
    bootstrap.initialize()
    with SessionLocal() as db:
        assert db.get(Source, "demo_identity") is None
        assert db.get(Source, "equipment") is not None
