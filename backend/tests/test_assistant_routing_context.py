"""Real routing/streaming boundaries with mock transport; no source or metadata DB."""
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app import assistant, chat_tools, project_settings
from app.analytics import chat_tools as analysis_tools
from app.auth import get_current_user


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setattr(chat_tools, "require_project", lambda _: None)
    monkeypatch.setattr(assistant, "llm_configuration", lambda: ("http://mock.invalid/v1", "test"))
    monkeypatch.setattr(assistant, "load_key", lambda _: ("mock-key", "1"))
    monkeypatch.setattr(assistant, "validate_llm_url", lambda _: "http://mock.invalid/v1/chat/completions")
    monkeypatch.setattr(project_settings, "managed_project", lambda _: None)
    monkeypatch.setattr(analysis_tools, "get_analysis_catalog", lambda: {
        "supported": True, "series": [{"family": "epm", "operation": "", "parameter": "PARA1"}],
        "recipe_operations": [], "issues": []})
    replies, observed = [], []
    real_client = httpx.AsyncClient

    def transport(request):
        observed.append(json.loads(request.content))
        assert replies, "Unexpected model call"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(replies.pop(0))}}]})

    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(transport), **kwargs))
    app = FastAPI()
    app.include_router(assistant.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test", active=True, role="editor")
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, replies=replies, observed=observed)


def events(response):
    assert response.status_code == 200
    return {block.splitlines()[0][7:]: json.loads(block.splitlines()[1][6:])
            for block in response.text.strip().split("\n\n")}


@pytest.mark.parametrize("kind", ["analysis", "clarification"])
def test_analysis_and_clarification_do_not_load_oversized_proposal_catalog(routing, monkeypatch, kind):
    def oversized_catalog(*args, **kwargs):
        pytest.fail("An analysis/clarification must not load the SQL/card catalog")
    monkeypatch.setattr(assistant, "proposal_catalog", oversized_catalog)
    async def analysis(call, adapter):
        yield "analysis", {"run_id": "analysis-only", "status": "completed"}
    monkeypatch.setattr(analysis_tools, "execute_analysis", analysis)
    routing.replies.append({"kind": "analysis", "analysis_kind": "statistics"} if kind == "analysis"
                           else {"kind": "clarification", "question": "Which period?"})
    result = events(routing.client.post("/api/chat", json={"message": "EPM PARA1 count"}))
    assert kind in result and "error" not in result
    assert len(routing.observed) == 1
    assert "datasets" not in routing.observed[0]["messages"][0]["content"].split("Registered context:\n")[1]


@pytest.mark.parametrize("supported,selected", [(False, None), (False, "company-data"), (True, "company-data")])
def test_company_schema_and_selected_dataset_use_generic_worker(routing, monkeypatch, supported, selected):
    from app import chat_data_analysis
    monkeypatch.setattr(analysis_tools, "get_analysis_catalog", lambda: {
        "supported": supported, "series": [], "recipe_operations": [], "issues": []})
    calls = []
    async def generic(request, adapter):
        calls.append(request)
        yield "data_analysis", {"status": "needs_input", "run_id": "actual-columns"}
    monkeypatch.setattr(chat_data_analysis, "run_workflow", generic)
    def legacy(*args, **kwargs):
        pytest.fail("A mismatched legacy schema must not execute")
    monkeypatch.setattr(analysis_tools, "execute_analysis", legacy)
    routing.replies.append({"kind": "analysis", "analysis_kind": "statistics", "parameter": "guessed-demo-name"})
    message = "CD_A MAIN과 LEAKAGE의 현재 데이터를 분석해줘"
    response = events(routing.client.post("/api/chat", json={"message": message, "dataset_id": selected}))
    assert response["data_analysis"]["status"] == "needs_input" and "error" not in response
    assert len(calls) == 1 and calls[0].message == message and calls[0].dataset_id == selected


@pytest.mark.parametrize("intent", ["query", "dataset", "card"])
def test_existing_skill_paths_receive_full_context_after_routing(routing, monkeypatch, intent):
    loaded = []
    def context(request, actor_id=None):
        assert len(routing.observed) == 1
        loaded.append(request)
        return {"datasets": [{"id": "existing", "version": 1}], "sources": [{"id": "source"}],
                "workspace_token": "workspace", "project_id": None, "custom_context": "preserved"}
    monkeypatch.setattr(assistant, "proposal_context", context)
    definition = {"name": "Query", "spec": {"source_id": "source", "raw_sql": "SELECT 1 AS value"}}
    preview = {"columns": [{"name": "value", "type": "integer"}], "rows": [{"value": 1}], "meta": {}, "next_cursor": None}
    monkeypatch.setattr(chat_tools, "validate_query", lambda *args: {"definition": definition, "columns": preview["columns"], "preview": preview})
    monkeypatch.setattr(chat_tools, "normalize_card_source", lambda card, *args: card)
    monkeypatch.setattr(chat_tools, "preview_card", lambda *args, **kwargs: {"rows": [{"value": 1}]})
    routing.replies.append({"kind": "route", "intent": intent})
    if intent == "card":
        routing.replies.append({"kind": "card", "explanation": "Existing dataset", "card": {
            "id": "card", "title": "Value", "dataset_id": "existing", "dataset_version": 1,
            "kind": "bar", "x": "value", "y": "value", "aggregate": "none"}})
    else:
        routing.replies.append({"kind": "query", "explanation": "Value", "name": "Query", "source_id": "source", "sql": "SELECT 1 AS value"})
        if intent == "dataset":
            routing.replies.append({"kind": "dataset", "explanation": "Reusable", "name": "Saved query"})
    result = events(routing.client.post("/api/chat", json={"message": intent}))
    assert "error" not in result, result
    assert result["proposal"]["kind"] == intent and len(loaded) == 1
    second_context = json.loads(routing.observed[1]["messages"][0]["content"].split("Registered context:\n")[1])
    assert second_context["custom_context"] == "preserved" and second_context["datasets"][0]["id"] == "existing"


def test_proposal_context_limit_still_rejects_oversized_skill_input(routing, monkeypatch):
    monkeypatch.setattr(assistant, "proposal_catalog", lambda *a, **k: ([{"id": "selected", "name": "x" * 100001}], []))
    monkeypatch.setattr(assistant, "knowledge_context", lambda *a, **k: {})
    from app import db
    monkeypatch.setattr(db, "workspace_token", lambda: "workspace")
    routing.replies.append({"kind": "route", "intent": "query"})
    result = events(routing.client.post("/api/chat", json={"message": "SQL please"}))
    assert "error" in result and "proposal" not in result and len(routing.observed) == 1
