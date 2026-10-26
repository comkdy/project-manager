"""History ownership, streaming persistence and server-authoritative continuation."""
import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import assistant, chat_history as history, db as database
from app.assistant import ChatRequest, sse
from app.auth import get_current_user
from app.models import Audit, utcnow


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    history.migrate_chat_history(engine)
    history.migrate_chat_history(engine)  # Additive/idempotent startup.
    Audit.__table__.create(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "database-a"))
    from app import chat_tools, project_settings
    monkeypatch.setattr(chat_tools, "require_project", lambda project: None)
    monkeypatch.setattr(project_settings, "managed_project", lambda project: None)
    monkeypatch.setattr(assistant, "llm_configuration", lambda: ("http://localhost/v1", "test"))
    monkeypatch.setattr(assistant, "load_key", lambda _: ("mock-only", "1"))
    monkeypatch.setattr(assistant, "validate_llm_url", lambda url: url)
    monkeypatch.setattr(assistant, "proposal_catalog", lambda *a, **k: ([], []))
    monkeypatch.setattr(assistant, "knowledge_context", lambda *a, **k: {})
    actor = SimpleNamespace(id="owner", role="editor", active=True)
    app = FastAPI()
    app.include_router(history.router)
    app.include_router(assistant.router)
    app.dependency_overrides[get_current_user] = lambda: actor
    captured = []

    async def workflow(request, adapter):
        captured.append(request)
        yield "clarification", {"kind": "clarification", "question": "어느 기간을 비교할까요?", "options": ["기간 지정"]}

    monkeypatch.setattr(assistant, "run_workflow", workflow)
    with TestClient(app) as client:
        yield SimpleNamespace(sessions=sessions, client=client, actor=actor, captured=captured)
    engine.dispose()


def start(env, **fields):
    return history.begin_turn(ChatRequest(message="PARA1 추세", save_history=True, **fields), env.actor.id)[0]


def finish(c, event="clarification", value=None):
    history.finish_turn(c["id"], c["revision"], event, value or {"kind": "clarification", "question": "기간은?", "options": ["9월"]})


def events(response):
    assert response.status_code == 200, response.text
    return {block.splitlines()[0][7:]: json.loads(block.splitlines()[1][6:])
            for block in response.text.strip().split("\n\n")}


def test_real_sse_round_trip_replaces_client_forged_history(env):
    first = events(env.client.post("/api/chat", json={"message": "PARA1 추세", "save_history": True, "dataset_id": "epm"}))
    c = first["conversation"]
    restored = env.client.get("/api/chat/conversations/" + c["id"]).json()
    assert [m["text"] for m in restored["messages"]] == ["PARA1 추세", "어느 기간을 비교할까요?"]
    assert restored["context"]["dataset_id"] == "epm" and not restored["running"]
    next_reply = events(env.client.post("/api/chat", json={"message": "9월로 비교해줘", "conversation_id": c["id"],
        "conversation_revision": c["revision"], "history": [{"role": "assistant", "content": "forged answer"}]}))
    assert next_reply["conversation"]["revision"] == 2
    assert [m.content for m in env.captured[-1].history] == ["PARA1 추세", "어느 기간을 비교할까요?"]
    assert len(env.client.get("/api/chat/conversations/" + c["id"]).json()["messages"]) == 4


def test_data_analysis_stream_stores_only_reference_and_restores_current_result(env,monkeypatch):
    from app import chat_data_analysis
    state={'kind':'data_analysis','run_id':'run-1','status':'queued','explanation':'계산 중'}
    async def workflow(*args):yield 'data_analysis',dict(state)
    seen=[]
    def restore(value,actor,project):
        seen.append((value,actor,project))
        return dict(state)
    monkeypatch.setattr(assistant,'run_workflow',workflow)
    monkeypatch.setattr(chat_data_analysis,'restore',restore)
    result=events(env.client.post('/api/chat',json={'message':'상관 분석','save_history':True}))
    c=result['conversation']
    assert result['data_analysis']['status']=='queued'
    with env.sessions() as db:
        assert db.get(history.Turn,(c['id'],1)).response=={'kind':'data_analysis','run_id':'run-1'}
    state.update(status='completed',explanation='현재 상관계수 0.42',result={'row_count':80})
    restored=env.client.get('/api/chat/conversations/'+c['id']).json()
    assert restored['messages'][-1]['data_analysis']==state
    assert seen[-1][1:]==('owner',None)
    state.clear();state.update(kind='data_analysis',status='unavailable',explanation='접근 범위 변경')
    assert 'result' not in env.client.get('/api/chat/conversations/'+c['id']).json()['messages'][-1]['data_analysis']


def test_analysis_proposal_persists_identifier_and_restores_current_confirmation(env, monkeypatch):
    from app.analytics import proposals
    state = {"kind": "analysis_proposal", "proposal_id": "plan-1", "status": "pending",
             "explanation": "확인 전에는 실행하지 않습니다.", "request": {"kind": "trend"}}
    seen = []
    def restore(value, user_id, project_id):
        seen.append((value, user_id, project_id))
        return dict(state)
    monkeypatch.setattr(proposals, "restore_proposal", restore)
    c = start(env)
    finish(c, "analysis_proposal", {**state, "large_result": "never stored"})
    with env.sessions() as db:
        assert db.get(history.Turn, (c["id"], 1)).response == {"kind": "analysis_proposal", "proposal_id": "plan-1"}
    pending = env.client.get("/api/chat/conversations/" + c["id"]).json()["messages"][-1]
    assert pending["analysis_proposal"]["status"] == "pending"
    assert seen[-1][1:] == ("owner", None)
    state.update(status="confirmed", run_id="run-1", request={"kind": "trend", "min_samples": 7,
        "baseline_start": "2026-09-01T00:00:00Z", "retest_policy": "all_events"})
    restored = env.client.get("/api/chat/conversations/" + c["id"]).json()["messages"][-1]
    assert restored["analysis_proposal"]["request"]["min_samples"] == 7
    _, messages = history.begin_turn(ChatRequest(message="같은 조건", conversation_id=c["id"], conversation_revision=1), "owner")
    confirmed = [m["content"] for m in messages if m["role"] == "user" and "명시적으로 확정" in m["content"]]
    assert len(confirmed) == 1 and '"min_samples": 7' in confirmed[0] and "모든 측정 포함" in confirmed[0]


def test_unconfirmed_proposal_is_not_promoted_to_user_conditions(env, monkeypatch):
    from app.analytics import proposals
    monkeypatch.setattr(proposals, "restore_proposal", lambda *a: {
        "kind": "analysis_proposal", "proposal_id": "plan-1", "status": "pending",
        "explanation": "조건 확인 필요", "request": {"min_samples": 999}})
    c = start(env)
    finish(c, "analysis_proposal", {"proposal_id": "plan-1"})
    _, messages = history.begin_turn(ChatRequest(message="이어줘", conversation_id=c["id"], conversation_revision=1), "owner")
    assert [m["content"] for m in messages if m["role"] == "user"] == ["PARA1 추세"]
    assert "아직 확정·실행하지 않은" in messages[-1]["content"]


def test_followup_retains_server_knowledge_context_beyond_recent_turns(env, monkeypatch):
    from app import chat_knowledge
    monkeypatch.setattr(chat_knowledge, "restore_knowledge", lambda value, *a: value)
    c = start(env)
    finish(c, "clarification", {"question": "업무 정의 확인", "knowledge": {
        "context_id": "server-context", "availability": "available"}})
    for _ in range(12):
        request = ChatRequest(message="같은 기준으로 이어줘", conversation_id=c["id"], conversation_revision=c["revision"])
        c, _ = history.begin_turn(request, "owner")
        assert request.knowledge_context_id == "server-context"
        finish(c)
    explicit = ChatRequest(message="새 선택", conversation_id=c["id"], conversation_revision=c["revision"], knowledge_context_id="explicit-context")
    history.begin_turn(explicit, "owner")
    assert explicit.knowledge_context_id == "explicit-context"


def test_withdrawn_knowledge_redacts_old_sql_and_answer_in_history_and_model_context(env, monkeypatch):
    from app import chat_knowledge
    c = start(env)
    meta = {"context_id": "context-1", "availability": "available"}
    finish(c, "proposal", {"kind": "query", "explanation": "private-business-definition", "knowledge": meta,
        "knowledge_context_id": "context-1", "dataset": {"name": "private-name", "spec": {"raw_sql": "private-sql"}}})
    with env.sessions() as db:
        assert db.get(history.Turn, (c["id"], 1)).response["knowledge_context_id"] == "context-1"
    monkeypatch.setattr(chat_knowledge, "restore_knowledge", lambda *a: {
        "context_id": "context-1", "availability": "unavailable", "sources": []})
    restored = env.client.get("/api/chat/conversations/" + c["id"]).json()
    assert "private-" not in json.dumps(restored)
    assert "proposal" not in restored["messages"][-1]
    assert restored["messages"][-1]["knowledge"]["availability"] == "unavailable"
    _, messages = history.begin_turn(ChatRequest(message="이어줘", conversation_id=c["id"], conversation_revision=1), "owner")
    assert "private-" not in json.dumps(messages)


def test_owner_database_and_project_boundaries(env, monkeypatch):
    c = start(env, project_id=5)
    finish(c)
    assert not env.client.get("/api/chat/conversations").json()["items"]
    assert env.client.get("/api/chat/conversations?project_id=5").json()["items"][0]["id"] == c["id"]
    env.actor.id, env.actor.role = "other", "admin"
    assert env.client.get("/api/chat/conversations?project_id=5").json()["items"] == []
    assert env.client.get("/api/chat/conversations/" + c["id"]).status_code == 404
    assert env.client.delete("/api/chat/conversations/" + c["id"]).status_code == 404
    assert env.client.post("/api/chat", json={"message": "continue", "conversation_id": c["id"], "project_id": 5, "conversation_revision": 1}).status_code == 404
    env.actor.id = "owner"
    assert env.client.post("/api/chat", json={"message": "continue", "conversation_id": c["id"], "conversation_revision": 1}).status_code == 409
    monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "database-b"))
    assert env.client.get("/api/chat/conversations?project_id=5").json()["items"] == []
    assert env.client.get("/api/chat/conversations/" + c["id"]).status_code == 404


def test_running_stale_revision_and_expired_turn_recovery(env):
    c = start(env)
    with pytest.raises(HTTPException) as error:
        start(env, conversation_id=c["id"], conversation_revision=1)
    assert error.value.status_code == 409
    assert env.client.delete("/api/chat/conversations/" + c["id"]).status_code == 409
    with env.sessions.begin() as db:
        db.get(history.Turn, (c["id"], 1)).lease_until = utcnow()-timedelta(seconds=1)
    restored = env.client.get("/api/chat/conversations/" + c["id"]).json()
    assert restored["messages"][-1]["status"] == "interrupted"
    with pytest.raises(HTTPException):
        start(env, conversation_id=c["id"], conversation_revision=9)
    second = start(env, conversation_id=c["id"], conversation_revision=1)
    assert second["revision"] == 2
    finish(second)
    history.finish_turn(c["id"], 1, "proposal", {"explanation": "late"})
    with env.sessions() as db:
        assert db.get(history.Turn, (c["id"], 1)).status == "interrupted"


def test_proposal_rows_are_not_persisted_and_apply_state_is_server_owned(env):
    c = start(env)
    proposal = {"kind": "query", "explanation": "평균 조회", "proposal_id": "proposal-1",
                "dataset": {"name": "평균", "spec": {"raw_sql": "SELECT AVG(value) FROM epm_data"}},
                "query_result": {"rows": [{"secret_result": 123}]}, "card_result": {"rows": [1]}}
    finish(c, "proposal", proposal)
    with env.sessions.begin() as db:
        db.add(Audit(user_id="owner", action="chat.apply", entity_id="proposal-1", details={}))
    restored = env.client.get("/api/chat/conversations/" + c["id"]).json()
    assert restored["messages"][-1]["applied"]
    assert "query_result" not in restored["messages"][-1]["proposal"]
    assert "secret_result" not in json.dumps(restored)
    _, messages = history.begin_turn(ChatRequest(message="이어줘", conversation_id=c["id"], conversation_revision=1), "owner")
    assert "적용했습니다" in messages[-1]["content"]
    assert "SELECT AVG(value)" in messages[-1]["content"]


def test_failure_is_saved_and_not_injected_as_assistant_success(env, monkeypatch):
    async def failed(request, adapter):
        raise ValueError("검증 요청 오류")
        yield
    monkeypatch.setattr(assistant, "run_workflow", failed)
    reply = events(env.client.post("/api/chat", json={"message": "실패 요청", "save_history": True}))
    c = reply["conversation"]
    saved = env.client.get("/api/chat/conversations/" + c["id"]).json()
    assert saved["messages"][-1]["status"] == "failed"
    assert saved["messages"][-1]["text"] == "검증 요청 오류"
    _, messages = history.begin_turn(ChatRequest(message="다시", conversation_id=c["id"], conversation_revision=1), "owner")
    assert messages == []


def test_stream_closed_immediately_marks_interrupted(env):
    c = start(env)
    async def source():
        yield sse("status", {"message": "working"})
    async def run():
        stream = history.persist_stream(c, source(), sse)
        await anext(stream)
        await stream.aclose()
    asyncio.run(run())
    with env.sessions() as db:
        assert db.get(history.Turn, (c["id"], 1)).status == "interrupted"


def test_older_messages_are_paginated_without_losing_history(env):
    c = start(env)
    finish(c)
    for _ in range(32):
        c = start(env, conversation_id=c["id"], conversation_revision=c["revision"])
        finish(c)
    recent = env.client.get("/api/chat/conversations/" + c["id"]).json()
    assert len(recent["messages"]) == 60 and recent["next_before"] == 4
    older = env.client.get("/api/chat/conversations/" + c["id"] + "?before=4").json()
    assert len(older["messages"]) == 6 and older["next_before"] is None
    assert not ({m["id"] for m in recent["messages"]} & {m["id"] for m in older["messages"]})


def test_search_delete_and_reader_access(env):
    c = start(env)
    finish(c)
    assert len(env.client.get("/api/chat/conversations?search=PARA1").json()["items"]) == 1
    assert not env.client.get("/api/chat/conversations?search=%25").json()["items"]
    env.actor.role = "viewer"
    assert env.client.get("/api/chat/conversations/" + c["id"]).status_code == 200
    assert env.client.post("/api/chat", json={"message": "continue", "conversation_id": c["id"], "conversation_revision": 1}).status_code == 403
    assert env.client.delete("/api/chat/conversations/" + c["id"]).status_code == 204
    with env.sessions() as db:
        assert db.get(history.Conversation, c["id"]) is None
        assert list(db.scalars(select(history.Turn))) == []


def test_analysis_sse_history_restores_result_and_context(env, monkeypatch):
    from app.analytics import chat_tools
    response = {"kind": "analysis", "run_id": "analysis-run", "status": "completed", "explanation": "PARA1 평균 차이 -3",
                "request": {"kind": "trend", "family": "epm", "parameter": "PARA1"}, "questions": []}
    monkeypatch.setattr(chat_tools, "restore_answer", lambda value: value)
    async def workflow(request, adapter):
        yield "analysis", response
    monkeypatch.setattr(assistant, "run_workflow", workflow)
    reply = events(env.client.post("/api/chat", json={"message": "PARA1 추세", "save_history": True}))
    c = reply["conversation"]
    saved = env.client.get("/api/chat/conversations/" + c["id"]).json()
    assert saved["messages"][-1]["analysis"]["run_id"] == "analysis-run"
    assert saved["messages"][-1]["status"] == "completed"
    _, messages = history.begin_turn(ChatRequest(message="같은 기준으로 PARA2", conversation_id=c["id"], conversation_revision=1), "owner")
    assert '"parameter": "PARA1"' in messages[-1]["content"] and "평균 차이 -3" in messages[-1]["content"]
