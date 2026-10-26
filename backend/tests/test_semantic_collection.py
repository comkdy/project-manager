"""No live provider calls or changes to the running app's business knowledge."""
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database
from app.auth import get_current_user
from app.models import Audit, DatabaseProfile, DatasetVersion, Relation, Source, User, utcnow
from app.semantic import collector_config as llm
from app.semantic import collector_service as service
from app.semantic.collector_models import CollectedDraft, CollectionDatasetScan, CollectionSettings, KnowledgeAnswer, KnowledgeQuestion
from app.semantic.collector_router import router
from app.semantic.collector_schemas import ExtractedKnowledge, GeneratedQuestions
from app.semantic.migration import migrate_semantic
from app.semantic.models import KnowledgeItem, KnowledgeVersion
from app.semantic.router import router as knowledge_router

BASE = "/api/semantic/collection"


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (User.__table__, Source.__table__, Relation.__table__, DatasetVersion.__table__, Audit.__table__, DatabaseProfile.__table__):
        table.create(engine)
    migrate_semantic(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "a", ignored_tables=()))
    monkeypatch.setattr(llm, "configured", lambda: True)
    with sessions.begin() as db:
        db.add_all([User(id="admin", username="admin", role="admin", name="관리자"),
                    User(id="reader", username="reader", role="viewer", name="담당자")])
        for key in ["a", "b"]:
            db.add(Source(id=key+"-src", connection_key=key, name="측정", schema_name="public", table_name="measurements",
                          primary_key="id", columns=[{"name": "id", "type": "integer"}, {"name": "result", "type": "text"}]))
            db.add(DatasetVersion(id=key+"-data", version=1, name=key+" 측정", spec={"source_id": key+"-src", "joins": []},
                                  columns=[{"name": "id", "type": "integer"}]))
    actor = SimpleNamespace(id="admin", role="admin", active=True)
    app = FastAPI()
    app.include_router(router)
    app.include_router(knowledge_router)
    app.dependency_overrides[get_current_user] = lambda: actor
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, actor=actor, monkeypatch=monkeypatch)
    engine.dispose()


def question(env, kind="term", subject="검사"):
    r = env.client.post(BASE+"/questions", json={"dataset_id": "a-data", "title": "검사란 무엇인가요?", "reason": "업무 의미 미정",
                                              "kind": kind, "topic": "other", "subject": subject})
    assert r.status_code == 201, r.text
    return r.json()


def extract(prompt, payload, model):
    a = payload["answers"][-1]
    return ExtractedKnowledge(name="검사", fields={"description": {"value": a["text"], "quote": a["text"], "answer_id": a["id"]}})


def answer(env, q, text="검사는 담당자가 기록한 작업입니다."):
    r = env.client.post(BASE+f"/questions/{q['id']}/answers", json={"expected_revision": q["revision"], "text": text})
    assert r.status_code == 202, r.text
    return r.json()


def draft(env):
    r = env.client.get(BASE+"/drafts")
    assert r.status_code == 200, r.text
    return r.json()[0]


def approve(env, d, **kwargs):
    return env.client.post(BASE+f"/drafts/{d['id']}/review", json={"expected_revision": d["revision"], "decision": "approve", "note": "담당자 답변 검토", **kwargs})


def test_question_detail_links_exact_answer_and_latest_draft_without_review_content(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", extract)
    q = question(env)
    first_answer = answer(env, q)
    first = draft(env)
    detail = env.client.get(BASE+f"/questions/{q['id']}").json()
    assert detail["draft"]["id"] == first["id"]
    assert detail["draft"]["answer_id"] == first_answer["answer_id"]
    next_answer = answer(env, detail, "검사는 담당자가 현장에서 확인한 작업입니다.")
    latest = env.client.get(BASE+f"/questions/{q['id']}").json()
    target = latest["draft"]
    assert target["id"] != first["id"]
    assert target["question_id"] == q["id"] and target["answer_id"] == next_answer["answer_id"]
    assert target["status"] == "pending"
    assert set(target) == {"id", "question_id", "answer_id", "status", "revision", "item_id", "item_version"}
    assert all("draft" not in row for row in env.client.get(BASE+"/questions").json())
    env.actor.id, env.actor.role = "reader", "viewer"
    assert env.client.get(BASE+f"/questions/{q['id']}").json()["draft"] == target
    assert env.client.get(BASE+f"/drafts/{target['id']}").status_code == 403
    env.actor.id, env.actor.role = "admin", "admin"
    linked = env.client.get(BASE+f"/drafts/{target['id']}").json()
    assert linked["question"]["id"] == q["id"] and linked["answer_id"] == target["answer_id"]
    approved = approve(env, linked)
    assert approved.status_code == 200, approved.text
    completed = env.client.get(BASE+f"/questions/{q['id']}").json()["draft"]
    assert completed["status"] == "approved"
    assert completed["item_id"] == approved.json()["item_id"]
    assert completed["item_version"] == approved.json()["item_version"]
    with database.use_source(database.SourceBinding(None, "b", ignored_tables=())):
        assert env.client.get(BASE+f"/questions/{q['id']}").status_code == 404
        assert env.client.get(BASE+f"/drafts/{target['id']}").status_code == 404


def configure(env, enabled=False):
    current = env.client.get(BASE+"/settings").json()
    r = env.client.post(BASE+"/settings", json={"expected_revision": current["revision"], "enabled": enabled, "interval_hours": 24,
                                              "max_questions": 3})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("base_url,model", [(" https://default.example/v1 ", " default-model "), ("", "")])
def test_collector_always_uses_default_url_and_model(monkeypatch, base_url, model):
    monkeypatch.setenv("KNOWLEDGE_LLM_API_KEY", "private-collector-key")
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("KNOWLEDGE_LLM_MODEL", "legacy-model")
    monkeypatch.setattr(llm.settings, "llm_base_url", base_url)
    monkeypatch.setattr(llm.settings, "llm_model", model)

    config, actual_url, actual_model = llm.configuration()

    assert (actual_url, actual_model) == (base_url.strip(), model.strip())
    assert config.llm_api_key.get_secret_value() == "private-collector-key"
    assert llm.configured() is bool(base_url.strip() and model.strip())


def test_settings_roles_scoping_and_no_secret(env, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_LLM_API_KEY", "private-collector-key")
    value = env.client.get(BASE+"/settings")
    assert value.status_code == 200
    assert "private-collector-key" not in value.text
    assert [d["id"] for d in value.json()["datasets"]] == ["a-data"]
    env.actor.id, env.actor.role = "reader", "viewer"
    for path in ["/settings", "/drafts"]:
        assert env.client.get(BASE+path).status_code == 403
    assert env.client.post(BASE+"/generate").status_code == 403
    assert env.client.get(BASE+"/questions").json() == []


def test_end_to_end_answer_draft_approval_and_provenance(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", extract)
    q = question(env)
    env.actor.id, env.actor.role = "reader", "viewer"
    answer(env, q)
    detail = env.client.get(BASE+f"/questions/{q['id']}").json()
    assert detail["status"] == "review"
    assert detail["answers"][0]["user_name"] == "담당자"
    assert env.client.get("/api/semantic/items").json()["items"] == []
    env.actor.id, env.actor.role = "admin", "admin"
    d = draft(env)
    assert d["proposal"]["scope"] == "database"
    assert d["evidence"]["description"]["quote"] == detail["answers"][0]["text"]
    env.actor.id, env.actor.role = "reader", "viewer"
    assert approve(env, d).status_code == 403
    env.actor.id, env.actor.role = "admin", "admin"
    r = approve(env, d)
    assert r.status_code == 200, r.text
    assert approve(env, d).status_code == 409
    with env.sessions() as db:
        assert db.get(CollectedDraft, d["id"]).original_proposal == d["proposal"]
        assert db.get(KnowledgeVersion, (r.json()["item_id"], 1)).status == "approved"
    assert env.client.get(BASE+f"/questions/{q['id']}").json()["status"] == "answered"


def test_unresolved_and_unsubstantiated_fields_never_approved(env, monkeypatch):
    def model(prompt, payload, schema):
        a = payload["answers"][0]
        return ExtractedKnowledge(name="미정 지표", fields={
            "description": {"value": a["text"], "answer_id": a["id"], "quote": a["text"]},
            "details.formula": {"value": "invented formula", "answer_id": a["id"], "quote": "not in answer"}},
            unresolved=["분모와 제외 조건 미정"], followups=["분모는 무엇인가요?"])
    monkeypatch.setattr(llm, "complete", model)
    answer(env, question(env, kind="metric"), "지표를 쓰지만 계산식은 모릅니다.")
    d = draft(env)
    assert not d["proposal"]["content"]["details"].get("formula")
    assert d["issues"]
    assert approve(env, d).status_code == 422
    assert "분모는 무엇인가요?" in d["question"]["followups"]
    assert any("계산식" in v for v in d["question"]["followups"])


def test_model_failure_preserves_answer_and_safe_retry(env, monkeypatch):
    def fail(*args):
        raise RuntimeError("secret-provider-key")
    monkeypatch.setattr(llm, "complete", fail)
    q = question(env)
    a = answer(env, q)
    detail = env.client.get(BASE+f"/questions/{q['id']}")
    assert "secret-provider-key" not in detail.text
    assert detail.json()["answers"][0]["status"] == "error"
    assert detail.json()["answers"][0]["text"]
    monkeypatch.setattr(llm, "complete", extract)
    assert env.client.post(BASE+f"/answers/{a['answer_id']}/retry").status_code == 202
    assert draft(env)["proposal"]["content"]["description"]


def test_additional_answer_supersedes_draft_without_erasing_history(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", extract)
    q = question(env)
    answer(env, q)
    previous = draft(env)
    q = env.client.get(BASE+f"/questions/{q['id']}").json()
    answer(env, q, "추가로 담당자가 결과를 확인합니다.")
    latest = draft(env)
    assert latest["id"] != previous["id"]
    assert len(latest["question"]["answers"]) == 2
    assert approve(env, previous).status_code == 409
    with env.sessions() as db:
        assert db.get(CollectedDraft, previous["id"]).status == "superseded"


def test_generation_change_detection_duplicate_prevention_and_leases(env, monkeypatch):
    calls = []
    def model(prompt, payload, schema):
        calls.append(payload)
        assert "answers" not in payload and "rows" not in str(payload)
        return GeneratedQuestions(questions=[{"dataset_id": "a-data", "title": "한 행은 무엇인가요?", "reason": "집계 단위 확인",
                                               "kind": "data", "topic": "grain", "subject": "row"}]*2)
    monkeypatch.setattr(llm, "complete", model)
    configure(env)
    r = env.client.post(BASE+"/generate")
    assert r.json()["created"] == 1, r.text
    assert env.client.post(BASE+"/generate").status_code == 429
    with env.sessions.begin() as db:
        cfg = db.get(CollectionSettings, "a")
        cfg.last_run = utcnow()-timedelta(minutes=2)
    assert env.client.post(BASE+"/generate").json()["status"] == "unchanged"
    assert len(calls) == 1
    with env.sessions.begin() as db:
        cfg = db.get(CollectionSettings, "a")
        cfg.lease_until = utcnow()+timedelta(minutes=2)
    assert env.client.post(BASE+"/generate").status_code == 409


def test_cross_database_hidden_sources_and_changed_dataset(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", extract)
    q = question(env)
    answer(env, q)
    d = draft(env)
    with database.use_source(database.SourceBinding(None, "b", ignored_tables=())):
        assert env.client.get(BASE+f"/questions/{q['id']}").status_code == 404
        assert approve(env, d).status_code == 404
    with env.sessions.begin() as db:
        source = db.get(Source, "a-src")
        source.available = False
    assert env.client.get(BASE+"/questions").json() == []
    assert env.client.get(BASE+"/drafts").json() == []
    with env.sessions.begin() as db:
        db.get(Source, "a-src").available = True
        db.add(DatasetVersion(id="a-data", version=2, name="새 데이터셋", spec={"source_id": "a-src", "joins": []}, columns=[]))
    assert approve(env, d).status_code == 422
    # New version may get a new question; the obsolete question isn't silently reused.
    assert question(env)["id"] != q["id"]


def test_conflict_requires_explicit_existing_item_and_keeps_versions(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", extract)
    q = question(env)
    answer(env, q)
    first = approve(env, draft(env)).json()
    answer(env, question(env, subject="다른 설명"), "담당자가 확인한 새 설명입니다.")
    d = draft(env)
    assert d["conflicts"][0]["id"] == first["item_id"]
    assert approve(env, d).status_code == 409
    r = approve(env, d, target_item_id=first["item_id"], target_revision=d["conflicts"][0]["revision"])
    assert r.status_code == 200, r.text
    assert r.json()["item_version"] == 2
    with env.sessions() as db:
        assert db.get(KnowledgeVersion, (first["item_id"], 1)).content["description"] != db.get(KnowledgeVersion, (first["item_id"], 2)).content["description"]


def test_disabled_schedule_invalid_owner_and_defer(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *args: pytest.fail("disabled schedule must not call model"))
    configure(env)
    assert service.generate_questions(scheduled=True)["status"] == "not_due"
    q = question(env)
    assert env.client.post(BASE+f"/questions/{q['id']}/defer", json={"expected_revision": 1, "days": 7}).json()["status"] == "deferred"
    assert env.client.post(BASE+f"/questions/{q['id']}/defer", json={"expected_revision": 1, "days": 0}).status_code == 409
    with env.sessions.begin() as db:
        cfg = db.get(CollectionSettings, "a")
        cfg.enabled, cfg.next_run, cfg.owner_id = True, utcnow()-timedelta(hours=1), "reader"
    assert service.generate_questions(scheduled=True)["status"] == "error"
    with env.sessions() as db:
        assert not db.get(CollectionSettings, "a").enabled


def test_transport_never_puts_key_in_prompt_or_exposes_provider_error(monkeypatch):
    from pydantic import SecretStr
    monkeypatch.setattr(llm, "configuration", lambda: (SimpleNamespace(llm_api_key=SecretStr("shared-secret-test")), "https://llm.test", "test"))
    monkeypatch.setattr(llm, "validate_llm_url", lambda _: "https://llm.test/chat/completions")
    real_client = httpx.Client
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(401, text="shared-secret-test")
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(llm.CollectionError) as exc:
        llm.complete("instruction", {"data": "metadata"}, GeneratedQuestions)
    assert "shared-secret-test" not in str(exc.value)
    assert "shared-secret-test" not in seen[0].content.decode()
    assert seen[0].headers["authorization"] == "Bearer shared-secret-test"


@pytest.mark.parametrize("content,valid", [('{"questions":[]}', True), ('```json\n{"questions":[]}\n```', True),
    ('설명입니다.\n```json\n{"questions":[]}\n```', False), ('```json\n{"questions":[],"unexpected":true}\n```', False)])
def test_transport_accepts_complete_json_fence_but_keeps_strict_validation(monkeypatch, content, valid):
    from pydantic import SecretStr
    monkeypatch.setattr(llm, "configuration", lambda: (SimpleNamespace(llm_api_key=SecretStr("shared-secret-test")), "https://llm.test", "test"))
    monkeypatch.setattr(llm, "validate_llm_url", lambda _: "https://llm.test/chat/completions")
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    if valid:
        assert llm.complete("instruction", {}, GeneratedQuestions).questions == []
    else:
        with pytest.raises(llm.CollectionError):
            llm.complete("instruction", {}, GeneratedQuestions)


def test_stale_answer_revision_and_unknowns_cannot_auto_publish(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a: ExtractedKnowledge(name="미정", unresolved=["정의 미정"]))
    q = question(env)
    answer(env, q, "모르겠습니다.")
    assert env.client.post(BASE+f"/questions/{q['id']}/answers", json={"expected_revision": 1, "text": "중복"}).status_code == 409
    d = draft(env)
    assert not d["proposal"]["content"]["description"]
    assert approve(env, d).status_code == 422
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeItem)) == 0


def test_manual_question_then_settings_and_failed_schedule_are_recoverable(env, monkeypatch):
    question(env)
    configure(env, enabled=True)
    with env.sessions.begin() as db:
        db.get(CollectionSettings, "a").next_run = utcnow()-timedelta(hours=1)
    monkeypatch.setattr(llm, "complete", lambda *args: GeneratedQuestions())
    assert service.generate_questions(scheduled=True)["status"] == "completed"
    with env.sessions() as db:
        cfg = db.get(CollectionSettings, "a")
        assert service.future(cfg.next_run)
        assert cfg.lease_token is None


def test_worker_uses_saved_database_not_current_browser_database(env, monkeypatch):
    from app.semantic import collector_worker as worker
    configure(env, enabled=True)
    with env.sessions.begin() as db:
        db.get(CollectionSettings, "a").next_run = utcnow()-timedelta(hours=1)
        db.add(DatabaseProfile(id="a", host="synthetic", port=5432, database="test", username="test", encrypted_url="unused"))
    seen = []
    monkeypatch.setattr(worker, "binding_for", lambda profile: database.SourceBinding(None, profile.id, ignored_tables=()))
    monkeypatch.setattr(worker, "generate_questions", lambda **kw: seen.append(database.source_database_key()))
    with database.use_source(database.SourceBinding(None, "b", ignored_tables=())):
        worker.tick()
        assert database.source_database_key() == "b"
    assert seen == ["a"]


def test_user_budget_and_inactive_answerer_do_not_stick_question(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *args: (_ for _ in ()).throw(llm.CollectionError("설정 확인 필요")))
    q = question(env)
    for i in range(10):
        q = env.client.get(BASE+f"/questions/{q['id']}").json()
        answer(env, q, f"테스트 답변 {i}")
    q = env.client.get(BASE+f"/questions/{q['id']}").json()
    assert env.client.post(BASE+f"/questions/{q['id']}/answers", json={"expected_revision": q["revision"], "text": "예산 초과"}).status_code == 429
    with env.sessions.begin() as db:
        a = db.scalar(select(KnowledgeAnswer).order_by(KnowledgeAnswer.created_at.desc()).limit(1))
        a.status = "queued"
        db.get(User, "admin").active = False
        db.get(KnowledgeQuestion, q["id"]).status = "structuring"
        identity = a.id
    service.process_answer(identity)
    with env.sessions() as db:
        assert db.get(KnowledgeQuestion, q["id"]).status == "open"
        assert db.get(KnowledgeAnswer, identity).status == "error"


def test_edit_review_preserves_original_and_audits_changes(env, monkeypatch):
    monkeypatch.setattr(llm, "complete", extract)
    answer(env, question(env))
    d = draft(env)
    original = d["proposal"]["content"]["description"]
    d["proposal"]["content"]["description"] = "검토자가 확인한 수정 설명"
    r = env.client.post(BASE+f"/drafts/{d['id']}", json={"expected_revision": d["revision"], "proposal": d["proposal"], "note": "담당자 재확인"})
    assert r.status_code == 200, r.text
    assert r.json()["original_proposal"]["content"]["description"] == original
    assert approve(env, d).status_code == 409
    assert approve(env, r.json()).status_code == 200
    assert env.client.get(BASE+f"/drafts/{d['id']}").json()["status"] == "approved"
    with env.sessions() as db:
        log = db.scalar(select(Audit).where(Audit.action == "semantic.collection.edit"))
        assert log.details["before"]["content"]["description"] == original


def test_invalid_generated_reference_does_not_poison_change_detection(env, monkeypatch):
    configure(env)
    monkeypatch.setattr(llm, "complete", lambda *args: GeneratedQuestions(questions=[{
        "dataset_id": "b-data", "title": "잘못된 대상", "reason": "테스트", "kind": "term", "topic": "other"}]))
    result = env.client.post(BASE+"/generate").json()
    assert result["status"] == "error"
    with env.sessions() as db:
        assert db.get(CollectionSettings, "a").last_digest == ""
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0


def test_schedule_can_be_disabled_after_key_or_datasets_are_removed(env, monkeypatch):
    current = configure(env, enabled=True)
    monkeypatch.setattr(llm, "configured", lambda: False)
    r = env.client.post(BASE+"/settings", json={"expected_revision": current["revision"], "enabled": False,
                                              "interval_hours": 24, "max_questions": 3})
    assert r.status_code == 200, r.text
    assert not r.json()["enabled"]


def add_dataset(env, identity, project_id=None):
    with env.sessions.begin() as db:
        db.add(DatasetVersion(id=identity, version=1, name=identity, project_id=project_id,
                              spec={"source_id": "a-src", "joins": []}, columns=[{"name": "id", "type": "integer"}]))


def reset_run_limit(env):
    with env.sessions.begin() as db:
        db.get(CollectionSettings, "a").last_run = utcnow() - timedelta(minutes=2)


def test_all_datasets_discovered_without_selection_and_new_datasets_included(env, monkeypatch):
    calls = []
    def model(prompt, payload, schema):
        calls.append(payload)
        return GeneratedQuestions(questions=[{"dataset_id": d["id"], "title": d["name"]+"의 의미는?",
            "reason": "업무 정의 확인", "kind": "term", "topic": "other", "subject": "의미"} for d in payload["datasets"]])
    monkeypatch.setattr(llm, "complete", model)
    add_dataset(env, "project-one", 1)
    cfg = configure(env, enabled=True)
    assert cfg["scope"] == "all_datasets" and "dataset_ids" not in cfg
    with env.sessions.begin() as db:
        db.get(CollectionSettings, "a").dataset_ids = ["removed-old-selection"]
    first = env.client.post(BASE+"/generate").json()
    assert (first["status"], first["scanned"], first["created"]) == ("completed", 2, 2)
    assert {d["id"] for p in calls for d in p["datasets"]} == {"a-data", "project-one"}
    assert all(len({d["project_id"] for d in p["datasets"]}) == 1 for p in calls)
    add_dataset(env, "project-two", 2)
    reset_run_limit(env)
    second = env.client.post(BASE+"/generate").json()
    assert second["scanned"] == 1 and second["total"] == 3
    assert calls[-1]["datasets"][0]["id"] == "project-two"
    env.actor.id, env.actor.role = "reader", "viewer"
    for params, expected in [({"project_id": 1}, {"project-one"}), ({"project_id": 2}, {"project-two"}),
                             ({"shared_only": True}, {"a-data"}), ({}, {"a-data", "project-one", "project-two"})]:
        r = env.client.get(BASE+"/questions", params=params)
        assert r.status_code == 200
        assert {q["dataset"]["id"] for q in r.json()} == expected
    q = env.client.get(BASE+"/questions?project_id=1").json()[0]
    assert q["dataset"]["project_id"] == 1
    assert env.client.get(BASE+f"/questions/{q['id']}").json()["dataset"]["project_id"] == 1
    assert env.client.get(BASE+"/questions?project_id=1&shared_only=true").status_code == 422


def test_large_inventory_rotates_batches_and_skips_unchanged(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "complete", lambda prompt, payload, schema: calls.append(payload) or GeneratedQuestions())
    for i in range(22):
        add_dataset(env, f"extra-{i:02}", 1 if i % 2 else 2)
    first = env.client.post(BASE+"/generate").json()
    assert (first["scanned"], first["remaining"], first["total"]) == (20, 3, 23)
    reset_run_limit(env)
    second = env.client.post(BASE+"/generate").json()
    assert (second["scanned"], second["remaining"]) == (3, 0)
    assert len({d["id"] for p in calls for d in p["datasets"]}) == 23
    call_count = len(calls)
    reset_run_limit(env)
    assert env.client.post(BASE+"/generate").json()["status"] == "unchanged"
    assert len(calls) == call_count
    with env.sessions.begin() as db:
        row = db.get(DatasetVersion, ("extra-00", 1))
        row.name = "변경된 정의"
    reset_run_limit(env)
    assert env.client.post(BASE+"/generate").json()["scanned"] == 1
    assert calls[-1]["datasets"][0]["id"] == "extra-00"


def test_model_cannot_relabel_project_question_as_shared(env, monkeypatch):
    add_dataset(env, "project-one", 1)
    def model(prompt, payload, schema):
        if payload["datasets"][0]["project_id"] is None:
            return GeneratedQuestions()
        return GeneratedQuestions(questions=[{"dataset_id": "a-data", "title": "프로젝트 전용 계산이란?",
            "reason": "잘못된 공통 소속", "kind": "term", "topic": "other", "subject": "전용 계산"}])
    monkeypatch.setattr(llm, "complete", model)
    assert env.client.post(BASE+"/generate").json()["status"] == "error"
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(CollectionDatasetScan)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0


def test_empty_inventory_can_schedule_and_failed_calls_keep_scan_pending(env, monkeypatch):
    with env.sessions.begin() as db:
        db.delete(db.get(DatasetVersion, ("a-data", 1)))
    assert configure(env, enabled=True)["enabled"]
    def fail(*args):
        raise llm.CollectionError("연결 확인 필요")
    monkeypatch.setattr(llm, "complete", fail)
    assert env.client.post(BASE+"/generate").json()["status"] == "empty"
    add_dataset(env, "new-data")
    reset_run_limit(env)
    assert env.client.post(BASE+"/generate").json()["status"] == "error"
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(CollectionDatasetScan)) == 0
    reset_run_limit(env)
    monkeypatch.setattr(llm, "complete", lambda *args: GeneratedQuestions())
    assert env.client.post(BASE+"/generate").json()["scanned"] == 1


def test_project_filter_runs_before_result_limit_and_tracks_current_scope(env):
    add_dataset(env, "project-one", 1)
    add_dataset(env, "project-two", 2)
    with env.sessions.begin() as db:
        datasets = service.inventory(db)
        db.add(KnowledgeQuestion(id="wanted", connection_key="a", fingerprint="wanted", title="소속 프로젝트 질문",
             reason="확인", kind="term", topic="other", subject="검증", dataset=datasets["project-one"], status="answered"))
        for i in range(301):
            db.add(KnowledgeQuestion(connection_key="a", fingerprint=f"other-{i}", title="다른 프로젝트 질문",
                 reason="확인", kind="term", topic="other", subject="검증", dataset=datasets["project-two"]))
    env.actor.id, env.actor.role = "reader", "viewer"
    assert [q["id"] for q in env.client.get(BASE+"/questions?project_id=1").json()] == ["wanted"]
    with env.sessions.begin() as db:
        db.get(DatasetVersion, ("project-one", 1)).project_id = 2
    assert env.client.get(BASE+"/questions/wanted").status_code == 404


@pytest.mark.parametrize("project_id, context_project, params", [
    (1, None, {"project_id": 1}),
    (None, 2, {"shared_only": True}),
])
def test_question_scope_keeps_visible_context_sources_from_other_scopes(env, project_id, context_project, params):
    if project_id is not None:
        add_dataset(env, "principal", project_id)
    principal_id = "principal" if project_id is not None else "a-data"
    with env.sessions.begin() as db:
        db.add(Source(id="a-context", connection_key="a", name="참조", schema_name="public",
                      table_name="context", primary_key="id", columns=[{"name": "id", "type": "integer"}]))
        db.add(DatasetVersion(id="context-data", version=1, name="참조", project_id=context_project,
                              spec={"source_id": "a-context", "joins": []}, columns=[{"name": "id", "type": "integer"}]))
        db.flush()
        datasets = service.inventory(db)
        db.add(KnowledgeQuestion(id="mixed-scope", connection_key="a", fingerprint="mixed-scope", title="두 원천의 의미",
             reason="분석 기준 확인", kind="term", topic="other", subject="검증", dataset=datasets[principal_id],
             context_source_ids=["a-src", "a-context"]))
        db.add(KnowledgeQuestion(id="other-scope", connection_key="a", fingerprint="other-scope", title="참조 원천 질문",
             reason="확인", kind="term", topic="other", subject="참조", dataset=datasets["context-data"]))
    response = env.client.get(BASE+"/questions", params=params)
    assert response.status_code == 200, response.text
    assert [q["id"] for q in response.json()] == ["mixed-scope"]
    assert response.json()[0]["dataset_changed"] is False
    assert env.client.get(BASE+"/questions/mixed-scope").status_code == 200
    # Cross-scope references remain subject to the full DB visibility policy.
    with database.use_source(database.SourceBinding(None, "a", ignored_tables=(("public", "context"),))):
        assert env.client.get(BASE+"/questions", params=params).json() == []
        assert env.client.get(BASE+"/questions/mixed-scope").status_code == 404
    assert [q["id"] for q in env.client.get(BASE+"/questions", params=params).json()] == ["mixed-scope"]


def test_answer_target_survives_storage_retry_and_model_structuring(env, monkeypatch):
    payloads = []
    def model(prompt, payload, schema):
        payloads.append(payload)
        if len(payloads) == 1:
            raise llm.CollectionError("테스트 재시도")
        return extract(prompt, payload, schema)
    monkeypatch.setattr(llm, "complete", model)
    q = question(env)
    target = {"kind": "column_value", "column": "result", "source_id": "a-src", "value": "RECHECK"}
    raw = "다시 확인이 필요한 상태입니다."
    r = env.client.post(BASE+f"/questions/{q['id']}/answers", json={"expected_revision": q["revision"], "text": raw, "targets": [target]})
    assert r.status_code == 202, r.text
    detail = env.client.get(BASE+f"/questions/{q['id']}").json()
    assert {c["column"] for c in detail["dataset"]["target_columns"]} == {"id", "result"}
    assert detail["answers"][0]["text"] == raw
    assert detail["answers"][0]["targets"][0]["kind"] == "column_value"
    assert detail["answers"][0]["targets"][0]["value"] == "RECHECK"
    assert payloads[0]["answers"][0]["targets"] == [target]
    assert env.client.post(BASE+f"/answers/{r.json()['answer_id']}/retry").status_code == 202
    assert payloads[1]["answers"][0]["targets"] == [target]
    reviewed = draft(env)
    assert reviewed["question"]["answers"][0]["targets"][0]["column"] == "result"
    assert reviewed["evidence"]["description"]["quote"] == raw
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeItem)) == 0


@pytest.mark.parametrize("target", [
    {"kind": "column", "column": "invented"},
    {"kind": "column", "column": "result", "source_id": "b-src"},
    {"kind": "column", "column": "id", "value": "PASS"},
    {"kind": "column_value", "column": "id"},
])
def test_answer_rejects_nonexistent_columns_and_ambiguous_value_shape(env, monkeypatch, target):
    def no_call(*args):
        pytest.fail("Invalid targets must not call the model")
    monkeypatch.setattr(llm, "complete", no_call)
    q = question(env)
    r = env.client.post(BASE+f"/questions/{q['id']}/answers", json={"expected_revision": q["revision"], "text": "설명", "targets": [target]})
    assert r.status_code == 422
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeAnswer)) == 0


def test_answer_rejects_stale_dataset_column_context(env):
    q = question(env)
    with env.sessions.begin() as db:
        db.get(DatasetVersion, ("a-data", 1)).name = "변경된 데이터셋"
    r = env.client.post(BASE+f"/questions/{q['id']}/answers", json={"expected_revision": q["revision"], "text": "설명", "targets": [{"kind": "column", "column": "id"}]})
    assert r.status_code == 409
