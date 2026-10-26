"""Real PostgreSQL acceptance tests; run only against the explicit local demo."""
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.db import SessionLocal, settings, source_engine
from app.models import DatasetVersion, User


@pytest.fixture(scope="module")
def api():
    if not settings.demo_mode or not settings.app_database_url or not settings.source_database_url:
        pytest.skip("Acceptance tests require the isolated DEMO_MODE PostgreSQL runtime")
    from app.main import app
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(settings, "auth_mode", "local_admin")
        with TestClient(app, client=("127.0.0.1", 50001)) as client:
            response = client.get("/api/auth/session")
            assert response.json()["status"] == "authenticated", response.text
            yield client


def dataset(api, source):
    return next(d for d in api.get("/api/bootstrap").json()["datasets"] if d["spec"]["source_id"] == source and d["version"] == 1)


def card_for(ds, **kwargs):
    return {"id": "qa-card", "title": "Acceptance chart", "dataset_id": ds["id"], "dataset_version": ds["version"],
            "kind": "bar", "renderer": "plotly", "x": "project_name", "y": "yield_pct",
            "aggregate": "yield", "filters": [], "options": {}, **kwargs}


def test_roles_and_session(api, monkeypatch):
    from app.main import app
    from app import sso_adapter
    identity = "qa-viewer-" + uuid.uuid4().hex[:12]
    monkeypatch.setattr(settings, "auth_mode", "sso")
    monkeypatch.setattr(settings, "sso_cookie_name", "company_sso")
    monkeypatch.setattr(sso_adapter, "verify_sso_cookie", lambda cookie: identity)
    monkeypatch.setattr(sso_adapter, "fetch_employee_profile", lambda value: sso_adapter.EmployeeProfile(value, "QA Viewer", "Test"))
    with TestClient(app, client=("127.0.0.1", 50002), headers={"Origin":"http://127.0.0.1:8000"}) as viewer:
        assert viewer.get("/api/bootstrap").status_code == 401
        viewer.cookies.set("company_sso", "verified-test-token")
        try:
            assert viewer.get("/api/bootstrap").status_code == 200
            assert viewer.post("/api/datasets", json={"name": "forbidden", "spec": {"source_id": "wafers", "columns": [], "filters": []}}).status_code == 403
            assert viewer.post("/api/sources/discover", json={}).status_code == 403
            assert viewer.get("/api/admin/users").status_code == 403
            assert viewer.post("/api/chat", json={"message": "카드 만들어줘"}).status_code == 403
            assert viewer.post("/api/chat/preview", json={}).status_code == 403
            assert viewer.post("/api/chat/apply", json={}).status_code == 403
            viewer.cookies.clear()
            assert viewer.get("/api/auth/me").status_code == 401
        finally:
            with SessionLocal.begin() as db:
                db.execute(delete(User).where(User.employee_id==identity))


def test_project_filters_and_keyset_pagination(api):
    ds = dataset(api, "inline_measurements")
    body = {"dataset_id": ds["id"], "dataset_version": ds["version"],
            "context": {"project_id": 1, "limit": 9, "sort": "value", "direction": "desc"}}
    first = api.post("/api/query", json=body)
    assert first.status_code == 200, first.text
    page1 = first.json()
    assert len(page1["rows"]) == 9
    assert all(r["project_id"] == 1 for r in page1["rows"])
    assert page1["next_cursor"]
    body["context"]["cursor"] = page1["next_cursor"]
    second = api.post("/api/query", json=body)
    assert second.status_code == 200, second.text
    page2 = second.json()
    assert not ({r["id"] for r in page1["rows"]} & {r["id"] for r in page2["rows"]})
    assert all(r["project_id"] == 1 for r in page2["rows"])


def test_weighted_yield_matches_source_sql(api):
    ds = dataset(api, "wafer_summary")
    result = api.post("/api/cards/query", json={"card": card_for(ds), "context": {"refresh": True}})
    assert result.status_code == 200, result.text
    with source_engine.connect() as connection:
        expected = dict(connection.execute(text("SELECT p.name, 100.0*count(*) FILTER (WHERE d.passed AND d.status='tested')/nullif(count(*) FILTER (WHERE d.status='tested'),0) FROM public.die_results d JOIN public.projects p ON p.id=d.project_id GROUP BY p.name")).all())
    assert len(result.json()["rows"]) == len(expected)
    for row in result.json()["rows"]:
        assert row["y"] == pytest.approx(float(expected[row["x"]]), rel=1e-8)


def test_default_dashboard_cards_query_and_optimistic_lock(api):
    board = api.get("/api/dashboards").json()
    assert len(board["cards"]) >= 5
    for card in board["cards"]:
        result = api.post("/api/cards/query", json={"card": card, "context": {}})
        assert result.status_code == 200, (card["title"], result.text)
        assert "mode" in result.json()["meta"]
    body = {k: board[k] for k in ("version", "cards", "layout")}
    # Exercise writes on a disposable copy, never the user's shared board.
    from app.models import Dashboard
    temporary_id = 'qa_lock_' + uuid.uuid4().hex[:12]
    with SessionLocal.begin() as db:
        db.add(Dashboard(id=temporary_id, project_id=board['project_id'], **body))
    try:
        saved = api.put(f"/api/dashboards/{temporary_id}", json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] == board["version"] + 1
        assert api.put(f"/api/dashboards/{temporary_id}", json=body).status_code == 409
        assert api.get('/api/dashboards').json() == board
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Dashboard).where(Dashboard.id == temporary_id))


@pytest.mark.parametrize("expression", [
    "(SELECT password_hash FROM app_users LIMIT 1)",
    "pg_sleep(15)", "value; DROP TABLE wafers", "unknown_column + 1",
    "current_setting('SOURCE_DATABASE_URL')", "sum(value) OVER ()",
])
def test_unsafe_or_unsupported_sql_rejected(api, expression):
    response = api.post("/api/datasets/preview", json={"spec": {"source_id": "inline_measurements", "columns": [{"name": "bad", "expression": expression}], "filters": []}})
    assert response.status_code in {400, 422}, response.text


def test_derived_preview_save_version_and_latest_card(api):
    spec = {"source_id": "inline_measurements", "columns": [
        {"name": "adjusted", "expression": "ROUND(CAST(value * 10 AS numeric), 3)"},
        {"name": "quality", "expression": "CASE WHEN value < lsl OR value > usl THEN 'FAIL' ELSE 'PASS' END"}], "filters": []}
    preview = api.post("/api/datasets/preview", json={"spec": spec, "context": {"project_id": 2, "limit": 5}})
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"] and "sql" in preview.json() and "before_rows" in preview.json()
    saved = api.post("/api/datasets", json={"name": "QA " + uuid.uuid4().hex[:8], "spec": spec})
    assert saved.status_code in {200, 201}, saved.text
    ds = saved.json()
    try:
        old_card = card_for(ds, kind="kpi", aggregate="avg", x=None, y="adjusted")
        first = api.post("/api/cards/query", json={"card": old_card, "context": {}})
        assert first.status_code == 200, first.text
        spec["columns"][0]["expression"] = "value * 100"
        update = {"name": ds["name"], "spec": spec, "expected_version": 1}
        version = api.post(f"/api/datasets/{ds['id']}/versions", json=update)
        assert version.status_code in {200, 201}, version.text
        assert version.json()["version"] == 2
        assert api.post(f"/api/datasets/{ds['id']}/versions", json=update).status_code == 409
        again = api.post("/api/cards/query", json={"card": old_card, "context": {"refresh": True}})
        assert again.status_code == 200, again.text
        assert again.json()["meta"]["dataset_version"] == 2
        assert again.json()["rows"][0]["value"] == pytest.approx(first.json()["rows"][0]["value"] * 10, rel=1e-6)
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id == ds["id"]))


def test_source_connection_cannot_write(api):
    with source_engine.connect() as connection:
        assert connection.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
        assert connection.execute(text("SELECT has_table_privilege(current_user, 'public.wafers', 'INSERT,UPDATE,DELETE')")).scalar() is False


def test_chart_cache_and_empty_result(api):
    ds = dataset(api, "wafer_summary")
    card = card_for(ds)
    body = {"card": card, "context": {"project_id": 1, "refresh": True}}
    first = api.post("/api/cards/query", json=body)
    assert first.status_code == 200
    body["context"]["refresh"] = False
    second = api.post("/api/cards/query", json=body)
    assert second.json()["meta"]["cached"] is True
    body["context"]["filters"] = [{"column": "lot_code", "op": "eq", "value": "nonexistent"}]
    empty = api.post("/api/cards/query", json=body)
    assert empty.status_code == 200 and empty.json()["rows"] == []


def test_seaborn_exports(api):
    ds = dataset(api, "wafer_summary")
    card = card_for(ds, title="프로젝트 수율", renderer="seaborn")
    for extension, expected in [("png", b"\x89PNG"), ("svg", b"<?xml")]:
        result = api.post("/api/render", json={"card": card, "context": {}, "format": extension})
        assert result.status_code == 200, result.text[:300] if result.status_code != 200 else ""
        assert result.content.startswith(expected)


def test_missing_llm_and_internal_proposal_contract(api, monkeypatch):
    import app.assistant as assistant
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "")
    missing = api.post("/api/chat", json={"message": "프로젝트별 수율 막대 그래프"})
    assert "event: error" in missing.text and "event: proposal" not in missing.text
    ds = dataset(api, "wafer_summary")
    proposal = {"kind": "card", "explanation": "프로젝트별 검사 완료 die 기준 수율입니다.", "card": card_for(ds)}
    original_client = httpx.AsyncClient
    observed = []
    def transport(request):
        observed.append(json.loads(request.content))
        answer = {"kind": "route", "intent": "card"} if len(observed) == 1 else proposal
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(assistant, "load_key", lambda user_id: ("integration-test-key", "test-revision"))
    monkeypatch.setattr(settings, "llm_model", "internal-test")
    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs))
    before = api.get("/api/dashboards").json()
    result = api.post("/api/chat", json={"message": "프로젝트별 수율 막대 그래프", "dataset_id": ds["id"]})
    assert "event: proposal" in result.text, result.text
    assert "event: error" not in result.text
    assert api.get("/api/dashboards").json() == before
    assert observed[0]["model"] == "internal-test"


def test_llm_rejects_metadata_and_non_server_addresses(monkeypatch):
    from app.llm_settings import validate_llm_url
    for address in ["169.254.169.254", "0.0.0.0", "224.0.0.1", "fe80::1", "::", "ff02::1", "::ffff:169.254.169.254"]:
        monkeypatch.setattr("app.llm_settings.socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 80))])
        with pytest.raises(ValueError):
            validate_llm_url("http://unsafe.example/v1")
