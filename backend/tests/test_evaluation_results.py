"""Measured results remain fixed through question, approval, and case retrieval."""
import copy

from sqlalchemy import select

from app import db as database
from app.analytics.models import AnalysisRun
from app.evaluation_analysis import execution_binding
from app.models import DatasetVersion, Source
from app.semantic import collector_config as llm
from app.semantic.collector_models import CollectedDraft, KnowledgeAnswer, KnowledgeQuestion
from app.semantic.collector_service import inventory
from app.semantic.evaluation_results import PREFIX, make_draft, review_state
from app.semantic.evaluations import retrieve_evaluation_cases
from test_evaluation_cases import publish
from test_semantic_collection import BASE, approve, env


def measured(env, monkeypatch):
    _, parent = publish(env, monkeypatch)
    with env.sessions.begin() as db:
        AnalysisRun.__table__.create(db.bind, checkfirst=True)
        data = inventory(db)["a-data"]
        source = db.get(Source, "a-src")
        snapshot = {"dataset": {k: getattr(source, k) for k in ("columns", "schema_name", "table_name")}}
        snapshot["dataset"]["source_id"] = source.id
        run = AnalysisRun(connection_key="a", kind="evaluation", status="completed", created_by="admin",
            request={"_workspace_token": database.workspace_token(), "_evaluation_dataset": data,
                "_evaluation_binding": execution_binding(db, data), "_owner_role": "admin",
                "_evaluation_case": {"id": parent["item_id"], "version": parent["item_version"],
                    "name": "공정 A 평가", "details": {"operation": "Operation A"}},
                "_evaluation_search": {"project_id": None, "dataset_id": "a-data"},
                "settings": {"method": "group_compare", "group_column": "recipe", "metrics": ["alpha"]}},
            source_snapshot=snapshot, result={"summary": "Recipe1: 유효 3건, alpha 평균 10. Recipe2: 유효 3건, alpha 평균 12. 평균 차이 2.",
                "row_count": 6, "method_version": "dataset-observation-v1", "captured_at": "2026-10-24T00:00:00Z"})
        db.add(run)
        db.flush()
        question = KnowledgeQuestion(connection_key="a", fingerprint="result:" + run.id,
            dataset=data, title="현재 데이터 분석", kind="evaluation", topic="other", subject=PREFIX + run.id,
            targets=[], status="review", created_by="admin")
        db.add(question)
        db.flush()
        answer = KnowledgeAnswer(question_id=question.id, text=run.result["summary"], user_id="admin", status="ready")
        db.add(answer)
        db.flush()
        draft = make_draft(db, question, answer, run)
        ids = run.id, question.id, draft.id
    return ids


def detail(env, identity):
    response = env.client.get(BASE + f"/drafts/{identity}")
    assert response.status_code == 200, response.text
    return response.json()


def test_completed_measurements_review_retrieve_and_origin(env, monkeypatch):
    run_id, question_id, draft_id = measured(env, monkeypatch)
    d = detail(env, draft_id)
    assert not d["issues"]
    assert d["question"]["execution_evidence"]["run_id"] == run_id
    assert d["proposal"]["content"]["details"]["analysis_result"].endswith("평균 차이 2.")
    with env.sessions() as db:
        assert len(retrieve_evaluation_cases(db, "관련 사례", dataset_id="a-data")["cases"]) == 1
    env.actor.id, env.actor.role = "reader", "viewer"
    assert approve(env, d).status_code == 403
    env.actor.id, env.actor.role = "admin", "admin"
    response = approve(env, d)
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        found = retrieve_evaluation_cases(db, "관련 사례", dataset_id="a-data")["cases"]
        result = next(c for c in found if c["id"] == response.json()["item_id"])
        assert result["execution_evidence"]["run_id"] == run_id
        assert "재계산하거나" not in result["limitations"][0]
        state = review_state(db, db.get(AnalysisRun, run_id))
        assert state["status"] == "approved" and state["question_id"] == question_id


def test_result_numbers_cannot_be_edited_into_approved_facts(env, monkeypatch):
    _, _, draft_id = measured(env, monkeypatch)
    d = detail(env, draft_id)
    proposal = copy.deepcopy(d["proposal"])
    proposal["content"]["details"]["analysis_result"] = "평균 차이 999."
    edited = env.client.post(BASE + f"/drafts/{draft_id}", json={"expected_revision": d["revision"],
        "proposal": proposal, "note": "원본과 다른 값"})
    assert edited.status_code == 200
    assert edited.json()["issues"]
    assert approve(env, edited.json()).status_code == 422


def test_rejection_and_followup_preserve_computation_without_llm(env, monkeypatch):
    run_id, question_id, draft_id = measured(env, monkeypatch)
    first = detail(env, draft_id)
    rejected = env.client.post(BASE + f"/drafts/{draft_id}/review", json={
        "expected_revision": first["revision"], "decision": "reject", "note": "비교 범위를 재확인해 주세요."})
    assert rejected.status_code == 200
    def no_model(*args, **kwargs):
        raise AssertionError("Computed facts must never be regenerated by an LLM")
    monkeypatch.setattr(llm, "complete", no_model)
    q = env.client.get(BASE + f"/questions/{question_id}").json()
    answer = env.client.post(BASE + f"/questions/{question_id}/answers", json={
        "expected_revision": q["revision"], "text": "동일 범위를 확인했습니다. 차이 999라는 내 추측은 계산 결과가 아닙니다."})
    assert answer.status_code == 202, answer.text
    with env.sessions() as db:
        next_state = review_state(db, db.get(AnalysisRun, run_id))
    second = detail(env, next_state["draft_id"])
    assert second["id"] != first["id"]
    assert second["proposal"]["content"]["details"] == first["proposal"]["content"]["details"]
    assert approve(env, second).status_code == 200


def test_changed_dataset_blocks_review_and_published_result_edits(env, monkeypatch):
    _, _, draft_id = measured(env, monkeypatch)
    approved = approve(env, detail(env, draft_id))
    assert approved.status_code == 200, approved.text
    item = next(i for i in env.client.get("/api/semantic/items").json()["items"] if i["id"] == approved.json()["item_id"])
    content = copy.deepcopy(item["content"])
    content["details"]["analysis_result"] = "수정된 계산값"
    edited = env.client.post(f"/api/semantic/items/{item['id']}/versions", json={"expected_revision": item["revision"], "content": content})
    assert edited.status_code == 422
    with env.sessions.begin() as db:
        old = db.get(DatasetVersion, ("a-data", 1))
        db.add(DatasetVersion(id=old.id, version=2, name=old.name, spec=old.spec, columns=old.columns))
    with env.sessions() as db:
        assert retrieve_evaluation_cases(db, "관련 사례", dataset_id="a-data")["cases"] == []
    q = detail(env, draft_id)["question"]
    assert q["execution_evidence"]["unavailable"] is True


def test_manual_case_cannot_claim_computed_result(env, monkeypatch):
    _, parent = publish(env, monkeypatch)
    item = env.client.get("/api/semantic/items").json()["items"][0]
    content = copy.deepcopy(item["content"])
    content["details"]["analysis_result"] = "계산했다고 주장한 숫자"
    response = env.client.post(f"/api/semantic/items/{parent['item_id']}/versions", json={
        "expected_revision": item["revision"], "content": content})
    assert response.status_code == 422
