"""End-to-end SQL definitions: shared source, project scope, persisted versions."""
import uuid

import httpx
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.db import SessionLocal, settings, source_engine
from app.models import DatasetVersion


@pytest.fixture(scope="module")
def api():
    if not settings.demo_mode or not settings.app_database_url:
        pytest.skip("Requires isolated demo PostgreSQL")
    from app.main import app
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(settings, "auth_mode", "local_admin")
        with TestClient(app, client=("127.0.0.1", 50006)) as client:
            assert client.get("/api/auth/session").json()["status"] == "authenticated"
            yield client


@pytest.fixture
def raw_dataset(api):
    spec = {"source_id": "inline_measurements", "raw_sql":
            "SELECT *, value * 1000 AS adjusted FROM public.inline_measurements WHERE project_id = 1 ORDER BY id"}
    response = api.post("/api/datasets", json={"name": "Raw SQL QA " + uuid.uuid4().hex[:8], "project_id": 1, "spec": spec})
    assert response.status_code == 201, response.text
    dataset = response.json()
    try:
        yield dataset
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id == dataset["id"]))


def test_raw_project_filter_aggregation_and_hidden_cursor(api, raw_dataset):
    body = {"dataset_id": raw_dataset["id"], "dataset_version": 1, "context": {"limit": 7}}
    first = api.post("/api/query", json=body)
    assert first.status_code == 200, first.text
    rows = first.json()["rows"]
    assert rows and all(r["project_id"] == 1 for r in rows)
    assert all(r["adjusted"] == pytest.approx(r["value"] * 1000) for r in rows)
    assert all("__row_id" not in r for r in rows)
    body["context"]["cursor"] = first.json()["next_cursor"]
    second = api.post("/api/query", json=body)
    assert second.status_code == 200, second.text
    assert not ({r["id"] for r in rows} & {r["id"] for r in second.json()["rows"]})
    card = {"id": "raw-sql-qa", "title": "Scoped SQL", "dataset_id": raw_dataset["id"], "dataset_version": 1,
            "kind": "kpi", "aggregate": "avg", "y": "adjusted"}
    result = api.post("/api/cards/query", json={"card": card, "context": {"refresh": True}})
    assert result.status_code == 200, result.text
    with source_engine.connect() as connection:
        expected = connection.execute(text("SELECT avg(value*1000) FROM public.inline_measurements WHERE project_id=1")).scalar()
    assert result.json()["rows"][0]["value"] == pytest.approx(float(expected))
    assert api.post("/api/cards/query", json={"card": card, "context": {"project_id": 2}}).status_code == 422


def test_raw_saved_versions_and_project_ownership_survive_initialize(api, raw_dataset):
    spec = {"source_id": "inline_measurements", "raw_sql": "SELECT id, value * 2000 AS adjusted FROM inline_measurements WHERE project_id=1 ORDER BY id"}
    response = api.post(f"/api/datasets/{raw_dataset['id']}/versions", json={
        "name": raw_dataset["name"], "spec": spec, "project_id": 1, "expected_version": 1})
    assert response.status_code == 201, response.text
    from app.bootstrap import initialize
    initialize()
    versions = api.get(f"/api/datasets/{raw_dataset['id']}/versions?project_id=1").json()
    assert [(v["version"], v["project_id"]) for v in versions] == [(2, 1), (1, 1)]
    values = []
    for version in (1, 2):
        response = api.post("/api/query", json={"dataset_id": raw_dataset["id"], "dataset_version": version,
                                                "context": {"limit": 3}})
        assert response.status_code == 200, response.text
        values.append(response.json()["rows"])
    for old, new in zip(*values):
        assert old["id"] == new["id"]
        assert new["adjusted"] == pytest.approx(old["adjusted"] * 2)


def test_form_sql_export_preserves_derived_filter_and_project(api):
    spec = {"source_id": "inline_measurements", "columns": [
        {"name": "id", "expression": "id"}, {"name": "adjusted", "expression": "value * 1000"}],
        "filters": [{"column": "adjusted", "op": "gt", "value": 100000}]}
    context = {"project_id": 2, "limit": 12}
    exported = api.post("/api/datasets/sql", json={"spec": spec, "context": context})
    assert exported.status_code == 200, exported.text
    sql = exported.json()["sql"]
    assert "project_id" in sql and "inline_measurements" in sql
    raw = {"source_id": "inline_measurements", "raw_sql": sql}
    before = api.post("/api/datasets/preview", json={"spec": spec, "context": context})
    after = api.post("/api/datasets/preview", json={"spec": raw, "context": context})
    assert before.status_code == after.status_code == 200, after.text
    assert before.json()["rows"] == after.json()["rows"]
    assert after.json()["canonical_sql"] == sql
    assert after.json()["meta"]["before_mode"] == "unavailable"


def test_raw_where_predicate_keeps_empty_results_and_original_schema(api):
    spec = {"source_id": "inline_measurements", "raw_sql":
            "SELECT id, CASE WHEN value > usl THEN 'HIGH' ELSE 'OK' END AS quality FROM inline_measurements WHERE FALSE"}
    result = api.post("/api/datasets/preview", json={"spec": spec, "context": {"project_id": 1}})
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == []
    assert {c["name"] for c in result.json()["columns"]} == {"id", "quality"}
    source = next(s for s in api.get("/api/bootstrap").json()["catalog"] if s["id"] == "inline_measurements")
    assert "quality" not in {c["name"] for c in source["columns"]}


def test_llm_catalog_and_raw_proposals_use_selected_project(api, raw_dataset, monkeypatch):
    import app.assistant as assistant
    from app.assistant import ChatRequest, proposal_catalog
    catalog, sources = proposal_catalog(ChatRequest(message="test", project_id=1, dataset_id=raw_dataset["id"]))
    assert {d["id"] for d in catalog} == {raw_dataset["id"]}
    assert "inline_measurements" in {s["table_name"] for s in sources}
    with pytest.raises(ValueError):
        proposal_catalog(ChatRequest(message="test", project_id=2, dataset_id=raw_dataset["id"]))
    card = {"id": "qa-pin", "title": "Pinned", "dataset_id": raw_dataset["id"], "dataset_version": 1, "kind": "kpi"}
    with pytest.raises(ValueError):
        proposal_catalog(ChatRequest(message="test", project_id=2, card=card))
    proposal = {"kind": "dataset", "explanation": "선택 프로젝트의 계산 컬럼", "dataset": {
        "name": "Chat SQL", "spec": {"source_id": "inline_measurements", "raw_sql":
        "SELECT *, value * 1000 AS from_chat FROM public.inline_measurements"}}}
    original_client = httpx.AsyncClient
    observed = []
    def transport(request):
        observed.append(json.loads(request.content))
        answers = [{"kind": "route", "intent": "dataset"}, {"kind": "query", "explanation": proposal["explanation"], "name": "Chat SQL", "source_id": "inline_measurements", "sql": proposal["dataset"]["spec"]["raw_sql"]}, {"kind": "dataset", "name": "Chat SQL", "explanation": proposal["explanation"]}]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answers[len(observed)-1])}}]})
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(assistant, "load_key", lambda user_id: ("raw-sql-test-key", "test-revision"))
    monkeypatch.setattr(settings, "llm_model", "internal-test")
    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs))
    response = api.post("/api/chat", json={"message": "SQL로 컬럼 추가", "project_id": 1, "dataset_id": raw_dataset["id"]})
    assert "event: proposal" in response.text and "event: error" not in response.text, response.text
    # Routing receives only selection metadata; the SQL-writing step receives
    # the registered catalog in its system context.
    context = json.loads(observed[1]["messages"][0]["content"].split("\nRegistered context:\n", 1)[1])
    assert context["project_id"] == 1
    assert all(d["project_id"] in (None, 1) for d in context["datasets"])
    assert "inline_measurements" in {s["table_name"] for s in context["sources"]}
