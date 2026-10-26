"""Real demo DB tools behind a deterministic internal LLM transport."""
import json
import uuid

import httpx
import pytest
from sqlalchemy import delete, event, func, select

from app import assistant
from app.db import SessionLocal, settings, source_engine
from app.models import Audit, Dashboard, DatasetVersion
from test_integration import api, card_for, dataset


SQL = "-- preserve this SQL\nSELECT lot_code, AVG(value) AS mean_value\nFROM public.inline_measurements\nWHERE project_id = 1 GROUP BY lot_code;"


def mock_llm(monkeypatch, replies):
    monkeypatch.setattr(assistant, "load_key", lambda user_id: ("workflow-test-key", "test-revision"))
    observed = []
    original = httpx.AsyncClient
    def transport(request):
        observed.append(json.loads(request.content))
        assert len(observed) <= len(replies), "Unbounded/unexpected extra model call"
        content = replies[len(observed) - 1]
        if not isinstance(content, str):
            content = json.dumps(content)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(settings, "llm_model", "internal-test")
    monkeypatch.setattr(assistant.httpx, "AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs))
    return observed


def events(response):
    result = {}
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2:
            result[lines[0].removeprefix("event: ")] = json.loads(lines[1].removeprefix("data: "))
    return result


def query(sql=SQL):
    return {"kind": "query", "explanation": "프로젝트 1의 Lot별 측정 평균", "name": "QA 평균",
            "source_id": "inline_measurements", "sql": sql}


def draft_card():
    return {"id": "new", "title": "Lot별 평균", "dataset_id": "draft-dataset", "dataset_version": 1,
            "kind": "bar", "x": "lot_code", "y": "mean_value", "aggregate": "none"}


def counts():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(DatasetVersion))


def definition(p):
    return {k: p[k] for k in ("kind", "explanation", "dataset", "card") if k in p}


def bundle(api):
    bootstrap = api.get("/api/bootstrap").json()
    return {"proposal": {"kind": "card", "dataset": {"name": "QA Chat Bundle",
            "spec": {"source_id": "inline_measurements", "raw_sql": SQL}}, "card": draft_card()},
            "project_id": 1, "workspace_token": bootstrap["workspace_token"]}


def needs_dataset(api, request, project_id=1):
    from app.query import assistant_catalog
    return {'kind':'needs_dataset','query_request':request,
            'reviewed_dataset_ids':[d['id'] for d in assistant_catalog(project_id=project_id)],
            'missing_capability':'원본 행을 요청한 집계·피벗 단위로 조회하는 재사용 데이터셋이 필요합니다.'}


def test_query_skill_executes_original_without_creating_dataset(api, monkeypatch):
    observed = mock_llm(monkeypatch, [{"kind": "route", "intent": "query"}, query()])
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    before = counts()
    event.listen(source_engine, "before_cursor_execute", capture)
    try:
        data = events(api.post("/api/chat", json={"message": "Lot별 평균 SQL", "project_id": 1}))
    finally:
        event.remove(source_engine, "before_cursor_execute", capture)
    assert "error" not in data, data
    p = data["proposal"]
    assert p["kind"] == "query" and p["steps"] == ["query"]
    assert p["dataset"]["spec"]["raw_sql"] == SQL
    assert any('"project_id" = 1' in statement for statement in statements) and p["query_result"]["rows"]
    assert counts() == before and len(observed) == 2
    assert "쿼리 작성 Skill" in observed[1]["messages"][0]["content"]


def test_dataset_skill_uses_validated_query_unchanged(api, monkeypatch):
    observed = mock_llm(monkeypatch, [{"kind": "route", "intent": "dataset"}, query(),
        {"kind": "dataset", "name": "Lot 평균 데이터셋", "explanation": "검증된 평균 조회를 재사용합니다."}])
    data = events(api.post("/api/chat", json={"message": "평균 데이터셋 생성", "project_id": 1}))
    assert "error" not in data, data
    assert data["proposal"]["steps"] == ["query", "dataset"]
    assert data["proposal"]["dataset"]["spec"]["raw_sql"] == SQL
    context = json.loads(observed[2]["messages"][0]["content"].split("\nRegistered context:\n", 1)[1])
    assert {c["name"] for c in context["validated_query"]["columns"]} == {"lot_code", "mean_value"}
    assert "rows" not in context["validated_query"]


def test_card_without_dataset_chains_all_three_skills_no_writes(api, monkeypatch):
    observed = mock_llm(monkeypatch, [
        {"kind": "route", "intent": "card"},
        needs_dataset(api, 'Lot별 평균을 lot_code, mean_value로 출력'),
        query(), {"kind": "dataset", "name": "Lot 평균", "explanation": "Lot 단위 평균"},
        {"kind": "card", "explanation": "이미 집계한 평균을 막대로 표시", "card": draft_card()},
    ])
    before = counts()
    data = events(api.post("/api/chat", json={"message": "Lot별 평균 카드", "project_id": 1}))
    assert "error" not in data, data
    p = data["proposal"]
    assert p["steps"] == ["card", "query", "dataset", "card"]
    assert p["dataset"]["spec"]["raw_sql"] == SQL and p["card_result"]["rows"]
    assert p["card"]["dataset_id"] == "draft-dataset" and counts() == before
    assert len(observed) == 5
    last = json.loads(observed[-1]["messages"][0]["content"].split("\nRegistered context:\n", 1)[1])
    assert last["draft_dataset"]["columns"] == p["query_result"]["columns"]
    assert "rows" not in last["draft_dataset"]


def test_existing_card_reuses_dataset_and_preserves_selected_id(api, monkeypatch):
    ds = dataset(api, "wafer_summary")
    selected = card_for(ds, id="selected-card")
    proposed = card_for(ds, id="model-invented", title="Updated")
    mock_llm(monkeypatch, [{"kind": "route", "intent": "card"},
                          {"kind": "card", "explanation": "제목 수정", "card": proposed}])
    p = events(api.post("/api/chat", json={"message": "제목 바꿔줘", "card": selected}))["proposal"]
    assert p["card"]["id"] == selected["id"] and "dataset" not in p


def test_chat_preview_matches_dashboard_period_and_can_explicitly_disable_it(api, monkeypatch):
    future = {'year':9998, 'week':1}
    body = bundle(api)
    body['period_start'] = future
    checked = api.post('/api/chat/preview', json=body)
    assert checked.status_code == 200, checked.text
    assert checked.json()['query_result']['rows']  # Dataset preview is still its authored SQL.
    chart = checked.json()['card_result']
    assert chart['rows'] == [] and chart['meta']['period']['start'] == future
    assert checked.json()['period_start'] == future

    observed = mock_llm(monkeypatch, [
        {'kind':'route', 'intent':'card'}, needs_dataset(api, 'Lot별 평균'),
        query(), {'kind':'dataset', 'name':'평균', 'explanation':'평균 조회'},
        {'kind':'card', 'explanation':'공통 기간 내 평균', 'card':draft_card()},
    ])
    response = events(api.post('/api/chat', json={'message':'평균 카드', 'project_id':1, 'period_start':future}))
    assert 'error' not in response, response
    assert response['proposal']['card_result']['rows'] == []
    assert response['proposal']['period_start'] == future
    context = json.loads(observed[-1]['messages'][0]['content'].split('\nRegistered context:\n', 1)[1])
    assert context['period_start'] == future

    body['proposal']['card']['period'] = {'use_dashboard':False}
    unbounded = api.post('/api/chat/preview', json=body)
    assert unbounded.status_code == 200, unbounded.text
    assert unbounded.json()['card_result']['rows'] and unbounded.json()['card_result']['meta']['period'] is None


@pytest.mark.parametrize("stage", ["route", "query", "card", "dataset"])
def test_clarification_stops_without_proposal(api, monkeypatch, stage):
    question = {"kind": "clarification", "question": "최초 검사 기준인가요?", "options": ["최초", "최종"]}
    replies = [] if stage == "route" else [{"kind": "route", "intent": "card" if stage == "card" else "dataset" if stage == "dataset" else "query"}]
    if stage == "dataset":
        replies.append(query())
    observed = mock_llm(monkeypatch, [*replies, question])
    before = counts()
    result = events(api.post("/api/chat", json={"message": "수율 조회", "project_id": 1}))
    assert {key: result["clarification"][key] for key in question} == question and "proposal" not in result
    if stage != 'route':
        assert result["clarification"]["knowledge"]["resolution"] == "missing_knowledge"
    assert len(observed) == len(replies) + 1 and counts() == before


def test_invalid_query_repaired_once_by_query_skill(api, monkeypatch):
    observed = mock_llm(monkeypatch, [{"kind": "route", "intent": "query"},
        query("SELECT missing_column FROM public.inline_measurements"), query()])
    result = events(api.post("/api/chat", json={"message": "평균 조회", "project_id": 1}))
    assert "proposal" in result and len(observed) == 3
    assert "validation feedback" in observed[-1]["messages"][-1]["content"]


def test_followup_format_retry_keeps_original_request_and_current_stage(api, monkeypatch):
    message = "앞선 SQL 그대로 장비 평균 데이터셋을 만들어줘. 카드는 아직 만들지 마."
    observed = mock_llm(monkeypatch, ["제안 내용: {", {"kind": "route", "intent": "dataset"},
        query(), {"kind": "dataset", "name": "장비 평균", "explanation": "검증된 SQL을 사용"}])
    result = events(api.post("/api/chat", json={"message": message, "project_id": 1, "history": [
        {"role": "user", "content": "SQL만 작성해줘"},
        {"role": "assistant", "content": json.dumps(query(), ensure_ascii=False)}]}))
    assert result["proposal"]["steps"] == ["query", "dataset"]
    assert len(observed) == 4
    retry = observed[1]["messages"][-1]["content"]
    assert message in retry and "Current internal step: route" in retry
    assert "internal validation error, not a new user request" in retry
    for call in observed:
        assert sum(m["role"] == "system" for m in call["messages"]) == 1
    routing_context = json.loads(observed[0]["messages"][0]["content"].split("\nRegistered context:\n", 1)[1])
    assert "sources" not in routing_context and "datasets" not in routing_context


def test_unsafe_query_rejected_after_bounded_retry(api, monkeypatch):
    unsafe = query("DELETE FROM public.inline_measurements")
    observed = mock_llm(monkeypatch, [{"kind": "route", "intent": "query"}, unsafe, unsafe])
    result = events(api.post("/api/chat", json={"message": "삭제해줘"}))
    assert "error" in result and "proposal" not in result and len(observed) == 3


def test_bundle_preview_conflict_apply_and_retry_are_atomic(api):
    body = bundle(api)
    before = counts()
    result = api.post("/api/chat/preview", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["card_result"]["rows"] and counts() == before
    board = api.get("/api/dashboards?project_id=1").json()
    request_id = str(uuid.uuid4())
    payload = dict(body, request_id=request_id, dashboard_id=board["id"], dashboard_version=board["version"] + 10)
    assert api.post("/api/chat/apply", json=payload).status_code == 409
    assert counts() == before
    payload["dashboard_version"] = board["version"]
    created = None
    try:
        response = api.post("/api/chat/apply", json=payload)
        assert response.status_code == 200, response.text
        created = response.json()["dataset_id"]
        assert counts() == before + 1
        retry = api.post("/api/chat/apply", json=payload)
        assert retry.json() == response.json() and counts() == before + 1
        saved = api.get("/api/dashboards?project_id=1").json()
        assert next(c for c in saved["cards"] if c["id"] == "new")["dataset_id"] == created
        version = api.get(f"/api/datasets/{created}/versions?project_id=1").json()[0]
        assert version["spec"]["raw_sql"] == SQL
        payload["proposal"]["dataset"]["name"] = "changed"
        assert api.post("/api/chat/apply", json=payload).status_code == 409
    finally:
        if created:
            with SessionLocal() as db:
                item = db.get(Dashboard, board["id"])
                item.cards, item.layout, item.version = board["cards"], board["layout"], board["version"]
                db.execute(delete(DatasetVersion).where(DatasetVersion.id == created))
                db.execute(delete(Audit).where(Audit.action == "chat.apply", Audit.entity_id == request_id))
                db.commit()


def test_stale_workspace_and_invalid_card_do_not_create_dataset(api):
    body = bundle(api)
    before = counts()
    stale = dict(body, workspace_token="old-db:0")
    assert api.post("/api/chat/preview", json=stale).status_code == 409
    assert api.post("/api/chat/apply", json=dict(stale, request_id=str(uuid.uuid4()))).status_code == 409
    body["proposal"]["card"]["y"] = "nonexistent"
    assert api.post("/api/chat/preview", json=body).status_code == 422
    assert api.post("/api/chat/apply", json=dict(body, request_id=str(uuid.uuid4()))).status_code == 422
    assert counts() == before


def test_other_project_dataset_cannot_be_used_in_common_card(api):
    created = api.post("/api/datasets", json={"name": "QA project scope", "project_id": 1,
                      "spec": {"source_id": "inline_measurements", "raw_sql": SQL}}).json()
    try:
        body = bundle(api)
        body["project_id"] = None
        body["proposal"].pop("dataset")
        body["proposal"]["card"]["dataset_id"] = created["id"]
        assert api.post("/api/chat/preview", json=body).status_code == 422
    finally:
        with SessionLocal() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id == created["id"]))
            db.commit()
