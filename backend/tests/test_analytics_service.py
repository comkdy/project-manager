"""Isolated metadata integration checks; no live DB or provider is accessed."""
from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database
from app.analytics import service, worker
from app.analytics.data import REQUIRED, TABLES
from app.analytics.models import AnalysisRun, migrate_analytics
from app.analytics.router import router
from app.auth import get_current_user
from app.models import Audit, DatabaseProfile, DatasetVersion, Relation, Source, User, utcnow
from app.semantic import collector_config, collector_router
from app.semantic.collector_models import CollectedDraft, KnowledgeAnswer, KnowledgeQuestion
from app.semantic.collector_router import router as questions_router
from app.semantic.collector_schemas import ExtractedKnowledge
from app.semantic.migration import migrate_semantic
from app.semantic.models import KnowledgeItem, KnowledgeVersion
from app.semantic.schemas import Content


BASE = "/api/analytics"


class NoSourceAccess:
    def connect(self, *args, **kwargs):
        raise AssertionError("Tests must never access a source database.")


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (User.__table__, Source.__table__, Relation.__table__, DatasetVersion.__table__,
                  Audit.__table__, DatabaseProfile.__table__):
        table.create(engine)
    migrate_semantic(engine)
    migrate_analytics(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    binding = database.SourceBinding(NoSourceAccess(), "a", ignored_tables=())
    monkeypatch.setattr(database, "active_source", binding)
    with sessions.begin() as db:
        db.add_all([User(id="admin", username="admin", role="admin", name="관리자"),
                    User(id="reader", username="reader", role="viewer", name="조회자")])
        for key in ("a", "b"):
            for family, table in TABLES.items():
                source_id = f"{key}-{family}"
                columns = [{"name": column, "type": "text"} for column in REQUIRED[family]]
                db.add(Source(id=source_id, connection_key=key, name=table, schema_name="public",
                              table_name=table, primary_key="record_id", columns=columns))
                db.add(DatasetVersion(id=f"{key}-{family}-data", version=1, name=f"{key} {table}",
                                      spec={"source_id": source_id, "joins": []}, columns=columns))
    actor = SimpleNamespace(id="admin", role="admin", active=True)
    app = FastAPI()
    app.include_router(router)
    app.include_router(questions_router)
    app.dependency_overrides[get_current_user] = lambda: actor
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, actor=actor, binding=binding)
    engine.dispose()


def trend_request(**overrides):
    return {"kind": "trend", "family": "epm", "parameter": "PARA1",
            "baseline_start": "2026-10-01T00:00:00Z", "baseline_end": "2026-10-03T00:00:00Z",
            "current_start": "2026-10-03T00:00:00Z", "current_end": "2026-10-05T00:00:00Z",
            "retest_policy": "latest_per_site", "min_samples": 1, "absolute_delta": 2, **overrides}


def submit(env, body):
    response = env.client.post(BASE + "/runs", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def measurement(value=10, at="2026-10-02T00:00:00Z", **overrides):
    return {"family": "epm", "operation": "", "parameter": "PARA1", "value": value,
            "measured_at": at, "lot_cd": "XXXA", "lot_id": "XXXA001", "wf_id": "01",
            "die_x": 1, "die_y": 2, "record_id": 1, **overrides}


def test_missing_definitions_create_real_deduplicated_questions_without_assumptions(env):
    request = {"kind": "trend", "family": "epm", "parameter": "PARA1"}
    first, second = submit(env, request), submit(env, request)
    assert first["status"] == second["status"] == "needs_input"
    assert first["result"] is None
    for key in ("baseline_start", "baseline_end", "current_start", "current_end", "retest_policy",
                "min_samples", "absolute_delta"):
        assert first["request"][key] is None
    first_ids = {question["id"] for question in first["questions"]}
    assert len(first_ids) == 3
    assert first_ids == {question["id"] for question in second["questions"]}
    inbox = env.client.get("/api/semantic/collection/questions")
    assert inbox.status_code == 200, inbox.text
    assert first_ids <= {question["id"] for question in inbox.json()}
    with env.sessions() as db:
        questions = list(db.scalars(select(KnowledgeQuestion)))
        assert {q.subject for q in questions} == {"analysis:period", "analysis:retest_policy", "analysis:change_criteria"}
        assert all(q.connection_key == "a" and q.dataset["id"] == "a-epm-data" for q in questions)
        assert db.scalar(select(func.count()).select_from(KnowledgeItem)) == 0
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 2


def test_complete_request_is_queued_and_keeps_explicit_criteria(env):
    run = submit(env, trend_request())
    assert run["status"] == "queued"
    assert run["request"]["min_samples"] == 1
    assert run["request"]["absolute_delta"] == 2
    assert run["questions"] == []
    assert run["result"] is None
    with env.sessions() as db:
        audit = db.scalar(select(Audit).where(Audit.entity_id == run["id"]))
        assert audit.action == "analytics.create"
        assert audit.details["missing"] == []


def test_history_list_omits_result_column_and_shares_question_lookups(env, monkeypatch):
    from sqlalchemy import event
    from unittest.mock import Mock
    first = submit(env, {"kind": "trend", "family": "epm", "parameter": "PARA1"})
    second = submit(env, {"kind": "trend", "family": "epm", "parameter": "PARA1"})
    with env.sessions.begin() as db:
        for identity in (first["id"], second["id"]):
            db.get(AnalysisRun, identity).result = {"large_result": "x" * 100000}
    inventory_spy = Mock(wraps=service.inventory)
    knowledge_spy = Mock(wraps=service.approved_knowledge)
    monkeypatch.setattr(service, "inventory", inventory_spy)
    monkeypatch.setattr(service, "approved_knowledge", knowledge_spy)
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = env.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = env.client.get(BASE + "/runs")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    listed = response.json()
    assert len(listed) == 2 and all(run["result"] is None and len(run["questions"]) == 3 for run in listed)
    assert inventory_spy.call_count == knowledge_spy.call_count == 1
    selects = [sql for sql in statements if "FROM app_analysis_runs" in sql]
    assert len(selects) == 1 and "app_analysis_runs.result" not in selects[0]
    assert len([sql for sql in statements if "FROM app_semantic_questions" in sql]) == 1
    assert len([sql for sql in statements if "FROM app_semantic_collected_drafts" in sql]) == 1
    # Detail still contains the complete stored result.
    assert env.client.get(BASE + f"/runs/{first['id']}").json()["result"] == {"large_result": "x" * 100000}


def test_worker_is_notified_only_for_a_committed_queued_run(env, monkeypatch):
    import importlib
    routes = importlib.import_module("app.analytics.router")
    notified = []

    def on_wake():
        with env.sessions() as db:
            queued = list(db.scalars(select(AnalysisRun).where(AnalysisRun.status == "queued")))
            assert queued
            notified.extend(r.id for r in queued)

    monkeypatch.setattr(routes, "wake_worker", on_wake)
    pending = submit(env, {"kind": "trend"})
    assert pending["status"] == "needs_input" and not notified
    queued = submit(env, trend_request())
    assert notified == [queued["id"]]


def test_missing_thresholds_allow_descriptive_comparison_but_create_question(env, monkeypatch):
    run = submit(env, trend_request(min_samples=None, absolute_delta=None))
    assert run["status"] == "queued"
    assert len(run["questions"]) == 1
    assert worker.claim() == (run["id"], "a")
    rows = [measurement(), measurement(20, at="2026-10-04T00:00:00Z", record_id=2)]
    monkeypatch.setattr(service, "extract", lambda snapshot, request: (rows, [], {"scope": "test"}))
    service.process_run(run["id"])
    detail = env.client.get(BASE + f"/runs/{run['id']}").json()
    assert detail["status"] == "completed"
    assert detail["result"]["series"][0]["delta"] == 10
    assert detail["result"]["series"][0]["status"] == "needs_criteria"
    assert detail["result"]["criteria"]["min_samples"] is None
    assert detail["result"]["criteria"]["absolute_delta"] is None


def test_viewer_and_stale_privileged_identity_cannot_execute(env):
    env.actor.id, env.actor.role = "reader", "viewer"
    assert env.client.post(BASE + "/runs", json={"kind": "statistics"}).status_code == 403
    # Even a stale request identity cannot override the current persisted role.
    env.actor.role = "admin"
    assert env.client.post(BASE + "/runs", json={"kind": "statistics"}).status_code == 403
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0


def test_results_are_scoped_to_current_database(env):
    run = submit(env, {"kind": "statistics"})
    with database.use_source(database.SourceBinding(NoSourceAccess(), "b", ignored_tables=())):
        assert env.client.get(BASE + f"/runs/{run['id']}").status_code == 404
        assert env.client.get(BASE + "/runs").json() == []
    assert env.client.get(BASE + f"/runs/{run['id']}").status_code == 200


@pytest.mark.parametrize("change", ["ignored", "unavailable", "schema", "addition"])
def test_hidden_or_changed_sources_block_persisted_results(env, change):
    run = submit(env, {"kind": "statistics", "family": "epm"})
    with env.sessions.begin() as db:
        stored = db.get(AnalysisRun, run["id"])
        stored.status, stored.result = "completed", {"private_result": 123}
        source = db.get(Source, "a-epm")
        if change == "unavailable":
            source.available = False
        elif change == "schema":
            source.columns = [c for c in source.columns if c['name'] != 'value']
        elif change == "addition":
            source.columns = source.columns + [{"name": "new_column", "type": "text"}]
    binding = database.SourceBinding(NoSourceAccess(), "a", ignored_tables=(("public", "epm_data"),) if change == "ignored" else ())
    with database.use_source(binding):
        response = env.client.get(BASE + f"/runs/{run['id']}")
        if change == 'addition':
            assert response.status_code == 200 and response.json()['result'] == {'private_result': 123}
            return
        assert response.status_code == 409
        assert "private_result" not in response.text
        assert env.client.get(BASE + "/runs").json() == []


def test_unapproved_knowledge_reference_rejected_without_creating_run(env):
    with env.sessions.begin() as db:
        db.add(KnowledgeItem(id="draft-definition", kind="data", connection_key="a"))
        db.add(KnowledgeVersion(item_id="draft-definition", version=1, status="draft",
                                content={"description": "unapproved"}, created_by="admin"))
    response = env.client.post(BASE + "/runs", json=trend_request(
        knowledge_refs=[{"item_id": "draft-definition", "version": 1}]))
    assert response.status_code == 422
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0


def test_claim_serializes_all_database_jobs_and_recovers_expired_lease(env):
    first = submit(env, {"kind": "statistics"})
    with database.use_source(database.SourceBinding(NoSourceAccess(), "b", ignored_tables=())):
        second = submit(env, {"kind": "statistics"})
    assert worker.claim() == (first["id"], "a")
    assert worker.claim() is None
    with env.sessions.begin() as db:
        db.get(AnalysisRun, first["id"]).lease_until = utcnow() - timedelta(seconds=1)
    assert worker.claim() == (second["id"], "b")
    assert worker.claim() is None
    with env.sessions() as db:
        expired, running = db.get(AnalysisRun, first["id"]), db.get(AnalysisRun, second["id"])
        assert expired.status == "failed" and expired.lease_until is None
        assert expired.completed_at is not None
        assert running.status == "running" and running.lease_until is not None


def test_process_stores_result_separately_without_source_or_knowledge_writes(env, monkeypatch):
    run = submit(env, {"kind": "statistics", "family": "epm"})
    assert worker.claim() == (run["id"], "a")
    calls = []
    rows = [measurement(10), measurement(20, wf_id="02", record_id=2), measurement(None, wf_id="03", record_id=3)]
    watermark = {"captured_at": "2026-10-05T00:00:00Z", "scope": "requested_rows"}

    def extract(snapshot, request):
        calls.append((snapshot, request))
        assert list(snapshot) == ["epm"]
        return rows, [], watermark

    monkeypatch.setattr(service, "extract", extract)
    service.process_run(run["id"])
    service.process_run(run["id"])  # A finished run is not calculated twice.
    assert len(calls) == 1
    response = env.client.get(BASE + f"/runs/{run['id']}")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert result["source_watermark"] == watermark
    assert result["result"]["series"][0]["statistics"]["mean"] == 15
    assert result["result"]["series"][0]["statistics"]["missing_count"] == 1
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeItem)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeVersion)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0
        assert db.get(AnalysisRun, run["id"]).lease_until is None


def test_process_exception_is_sanitized_in_persisted_and_api_errors(env, monkeypatch):
    run = submit(env, {"kind": "statistics", "family": "epm"})
    worker.claim()

    def fail(*args):
        raise RuntimeError("postgresql://secret-user:private-password@private-host/private-db")

    monkeypatch.setattr(service, "extract", fail)
    service.process_run(run["id"])
    response = env.client.get(BASE + f"/runs/{run['id']}")
    assert response.json()["status"] == "failed"
    assert response.json()["result"] is None
    assert "private-password" not in response.text and "private-host" not in response.text
    with env.sessions() as db:
        stored = db.get(AnalysisRun, run["id"])
        assert stored.error and "private-password" not in stored.error
        assert stored.lease_until is None


def test_process_rechecks_actor_before_extracting_source(env, monkeypatch):
    run = submit(env, {"kind": "statistics", "family": "epm"})
    worker.claim()
    with env.sessions.begin() as db:
        db.get(User, "admin").active = False

    def forbidden(*args):
        pytest.fail("Revoked actors must not reach source extraction.")

    monkeypatch.setattr(service, "extract", forbidden)
    service.process_run(run["id"])
    with env.sessions() as db:
        assert db.get(AnalysisRun, run["id"]).status == "failed"


def test_crossover_result_remains_needs_input_and_creates_question(env, monkeypatch):
    run = submit(env, {"kind": "split", "operation": "oper1", "recipe_a": "A",
                       "recipe_b": "B", "start": "2026-10-01T00:00:00Z", "end": "2026-10-05T00:00:00Z",
                       "retest_policy": "latest_per_site", "min_samples": 1, "absolute_delta": 1})
    worker.claim()
    rows = [measurement(10), measurement(20, at="2026-10-04T00:00:00Z", record_id=2)]
    histories = [{"lot_cd": "XXXA", "lot_id": "XXXA001", "wf_id": "01", "operation": "oper1",
                  "recipe": recipe, "measured_at": at, "record_id": index}
                 for index, recipe, at in ((1, "A", "2026-10-01T00:00:00Z"), (2, "B", "2026-10-03T00:00:00Z"))]
    monkeypatch.setattr(service, "extract", lambda snapshot, request: (rows, histories, {}))
    service.process_run(run["id"])
    detail = env.client.get(BASE + f"/runs/{run['id']}").json()
    assert detail["status"] == "needs_input"
    assert detail["result"]["excluded_ambiguous_wafer_count"] == 1
    assert len(detail["questions"]) == 1
    with env.sessions() as db:
        question = db.get(KnowledgeQuestion, detail["questions"][0]["id"])
        assert question.subject == "analysis:recipe_crossover"
        assert question.status == "open"


def test_regression_changed_dataset_creates_new_answerable_questions(env, monkeypatch):
    request = {"kind": "trend", "family": "epm", "parameter": "PARA1"}
    first = submit(env, request)
    with env.sessions.begin() as db:
        previous = db.get(DatasetVersion, ("a-epm-data", 1))
        db.add(DatasetVersion(id=previous.id, version=2, name="updated EPM dataset",
                              spec=previous.spec, columns=previous.columns))
    second = submit(env, request)
    old_ids = {question["id"] for question in first["questions"]}
    new_ids = {question["id"] for question in second["questions"]}
    assert len(new_ids) == 3 and not old_ids.intersection(new_ids)
    question_id = second["questions"][0]["id"]
    detail = env.client.get(f"/api/semantic/collection/questions/{question_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["dataset"]["version"] == 2
    assert detail.json()["dataset_changed"] is False
    monkeypatch.setattr(collector_router, "process_bound", lambda identity, binding: None)
    response = env.client.post(f"/api/semantic/collection/questions/{question_id}/answers", json={
        "expected_revision": detail.json()["revision"], "text": "두 기간은 담당자가 별도 확인하여 입력합니다."})
    assert response.status_code == 202, response.text
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeAnswer).where(
            KnowledgeAnswer.question_id == question_id)) == 1


@pytest.mark.parametrize("body", [
    {"kind": "statistics", "recipe_a": "RCP1"},
    {"kind": "statistics", "lot_ids_a": ["XXXA001"]},
    trend_request(start="2026-10-01T00:00:00Z"),
    trend_request(end="2026-10-05T00:00:00Z"),
    {"kind": "statistics", "family": "epm", "operation": "oper1"},
    trend_request(operation="oper1"),
    {"kind": "split", "family": "epm"},
])
def test_regression_inapplicable_kind_fields_are_rejected(env, body):
    response = env.client.post(BASE + "/runs", json=body)
    assert response.status_code == 422, response.text
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0


def test_regression_question_context_hides_excluded_secondary_source(env):
    run = submit(env, {"kind": "trend"})
    ids = {question["id"] for question in run["questions"]}
    with env.sessions() as db:
        question = db.get(KnowledgeQuestion, next(iter(ids)))
        assert set(question.context_source_ids) == {"a-inline", "a-epm"}
        assert question.dataset["id"] == "a-epm-data"
    # EPM remains visible, but the questions also describe excluded Inline data.
    with database.use_source(database.SourceBinding(NoSourceAccess(), "a", ignored_tables=(("public", "inline_data"),))):
        inbox = env.client.get("/api/semantic/collection/questions")
        assert inbox.status_code == 200, inbox.text
        assert not ids.intersection(question["id"] for question in inbox.json())
        assert env.client.get(f"/api/semantic/collection/questions/{next(iter(ids))}").status_code == 404
        with env.sessions() as db:
            assert service.questions_json(db, db.get(AnalysisRun, run["id"])) == []


@pytest.mark.parametrize("change", ["revoked", "superseded"])
def test_regression_approved_question_uses_only_current_canonical_definition(env, change):
    run = submit(env, trend_request(min_samples=None, absolute_delta=None))
    question_id = run["questions"][0]["id"]
    canonical = Content(name="승인된 분석 조건", description="담당자가 승인한 현재 설명").for_kind("term")
    with env.sessions.begin() as db:
        db.add(KnowledgeItem(id="approved-condition", kind="term", connection_key="a", published_version=1))
        db.add(KnowledgeVersion(item_id="approved-condition", version=1, content=canonical,
                                status="approved", created_by="admin"))
        db.add(KnowledgeAnswer(id="answer-condition", question_id=question_id, text="original answer",
                               user_id="admin", status="ready"))
        db.add(CollectedDraft(question_id=question_id, answer_id="answer-condition", status="approved",
                              proposal={"content": {"description": "stale copied proposal"}},
                              original_proposal={}, evidence={}, reviewed_at=utcnow(),
                              item_id="approved-condition", item_version=1))
        db.get(KnowledgeQuestion, question_id).status = "answered"
    response = env.client.get(BASE + f"/runs/{run['id']}")
    approved = response.json()["questions"][0]["approved_definition"]
    assert approved["content"] == canonical
    assert "stale copied proposal" not in response.text
    with env.sessions.begin() as db:
        if change == "revoked":
            db.get(KnowledgeVersion, ("approved-condition", 1)).status = "draft"
        else:
            item = db.get(KnowledgeItem, "approved-condition")
            item.head_version = item.published_version = 2
            db.add(KnowledgeVersion(item_id=item.id, version=2, content=Content(
                name="다음 승인 조건", description="후속 버전").for_kind("term"), status="approved", created_by="admin"))
    response = env.client.get(BASE + f"/runs/{run['id']}")
    assert response.status_code == 200, response.text
    assert "approved_definition" not in response.json()["questions"][0]


def test_regression_validation_purpose_keeps_criteria_unknown_without_inbox_writes(env, monkeypatch):
    run = submit(env, trend_request(purpose="validation", min_samples=None, absolute_delta=None))
    assert run["status"] == "queued" and run["questions"] == []
    worker.claim()
    rows = [measurement(10), measurement(20, at="2026-10-04T00:00:00Z", record_id=2)]
    monkeypatch.setattr(service, "extract", lambda snapshot, request: (rows, [], {}))
    service.process_run(run["id"])
    detail = env.client.get(BASE + f"/runs/{run['id']}").json()
    assert detail["status"] == "completed"
    assert detail["result"]["purpose"] == "validation"
    assert detail["result"]["series"][0]["status"] == "needs_criteria"
    assert detail["result"]["criteria"]["min_samples"] is None
    assert detail["result"]["criteria"]["absolute_delta"] is None
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeItem)) == 0


def test_regression_validation_crossover_does_not_write_inbox(env, monkeypatch):
    run = submit(env, {"kind": "split", "purpose": "validation", "operation": "oper1", "recipe_a": "A",
                       "recipe_b": "B", "start": "2026-10-01T00:00:00Z", "end": "2026-10-05T00:00:00Z",
                       "retest_policy": "latest_per_site"})
    worker.claim()
    histories = [{"lot_cd": "XXXA", "lot_id": "XXXA001", "wf_id": "01", "operation": "oper1",
                  "recipe": recipe, "measured_at": at, "record_id": index}
                 for index, recipe, at in ((1, "A", "2026-10-01T00:00:00Z"), (2, "B", "2026-10-01T12:00:00Z"))]
    monkeypatch.setattr(service, "extract", lambda snapshot, request: ([measurement()], histories, {}))
    service.process_run(run["id"])
    detail = env.client.get(BASE + f"/runs/{run['id']}").json()
    assert detail["status"] == "needs_input" and detail["questions"] == []
    assert detail["result"]["requires_crossover_decision"] is True
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0


@pytest.mark.parametrize("change", ["revoked", "superseded"])
def test_regression_natural_language_condition_approves_without_grain_or_binding(env, monkeypatch, change):
    run = submit(env, trend_request(min_samples=None, absolute_delta=None))
    question_id = run["questions"][0]["id"]
    detail = env.client.get(f"/api/semantic/collection/questions/{question_id}").json()
    assert detail["kind"] == "term"
    answer = "이 비교에서는 각 기간의 유효 측정이 5건 이상이고 평균 차이가 2 이상이면 변화로 봅니다."

    def extract(prompt, payload, schema):
        user_answer = payload["answers"][-1]
        assert payload["kind"] == "term" and payload["allowed_details"] == []
        return ExtractedKnowledge(name="PARA1 변화 검토 기준", fields={"description": {
            "value": user_answer["text"], "quote": user_answer["text"], "answer_id": user_answer["id"]}})

    monkeypatch.setattr(collector_config, "configured", lambda: True)
    monkeypatch.setattr(collector_config, "complete", extract)
    response = env.client.post(f"/api/semantic/collection/questions/{question_id}/answers", json={
        "expected_revision": detail["revision"], "text": answer})
    assert response.status_code == 202, response.text
    answer_id = response.json()["answer_id"]
    drafts = env.client.get("/api/semantic/collection/drafts").json()
    assert len(drafts) == 1
    draft = drafts[0]
    linked = env.client.get(f"/api/semantic/collection/questions/{question_id}").json()["draft"]
    assert linked["id"] == draft["id"] and linked["answer_id"] == answer_id
    assert linked["question_id"] == question_id
    assert draft["issues"] == []
    assert draft["proposal"]["kind"] == "term" and draft["proposal"]["scope"] == "database"
    assert draft["proposal"]["content"]["details"] == {}
    response = env.client.post(f"/api/semantic/collection/drafts/{draft['id']}/review", json={
        "expected_revision": draft["revision"], "decision": "approve", "note": "담당자 조건 확인"})
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        item = db.get(KnowledgeItem, response.json()["item_id"])
        approved = db.get(KnowledgeVersion, (item.id, item.published_version))
        assert item.connection_key == "a" and approved.status == "approved"
        assert approved.content["description"] == answer
        assert approved.content["details"] == {}
    latest = env.client.get(BASE+f"/runs/{run['id']}").json()
    assert latest["id"] == run["id"] and latest["request"] == run["request"]
    assert latest["request"]["min_samples"] is None and latest["request"]["absolute_delta"] is None
    criterion = latest["questions"][0]["approved_definition"]
    assert criterion["content"]["description"] == answer
    assert criterion["item_id"] == response.json()["item_id"]
    assert criterion["version"] == response.json()["item_version"]
    with env.sessions.begin() as db:
        if change == "revoked":
            db.get(KnowledgeVersion, (criterion["item_id"], criterion["version"])).status = "draft"
        else:
            item = db.get(KnowledgeItem, criterion["item_id"])
            item.head_version = item.published_version = criterion["version"] + 1
            db.add(KnowledgeVersion(item_id=item.id, version=item.published_version,
                content=Content(name="새 승인 조건", description="다른 기준").for_kind("term"),
                status="approved", created_by="admin"))
    refreshed = env.client.get(BASE+f"/runs/{run['id']}").json()
    assert refreshed["request"] == run["request"]
    assert "approved_definition" not in refreshed["questions"][0]
