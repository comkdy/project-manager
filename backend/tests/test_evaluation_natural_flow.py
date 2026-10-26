"""Scope-only registration, grounded terms, conversation and legacy compatibility."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app import catalog
from app.models import DatasetVersion, Source
from app.semantic import collector_config as llm
from app.semantic.collector_models import KnowledgeAnswer, KnowledgeQuestion
from app.semantic.collector_schemas import EvaluationMention
from app.semantic.evaluation_extraction import EvaluationPassages, prepare, restore
from app.semantic.evaluation_terms import knowledge_candidates
from app.semantic.models import BindingHead, BindingVersion, KnowledgeItem, KnowledgeVersion
from app.semantic.schemas import Content
from app.semantic.service import capture_binding, catalog_snapshot
from test_evaluation_cases import REPORT, cases, extract_case
from test_semantic_collection import BASE, approve, draft, env
from test_evaluation_analysis import setup, prepare as prepare_analysis, execute, fake_result


@pytest.fixture(autouse=True)
def projects(monkeypatch):
    monkeypatch.setattr(catalog, "projects_list", lambda: [{"id": 17, "name": "XXXA"}, {"id": 18, "name": "XXXB"}])


def definition(env, identity="width", name="A 공정 선폭", aliases=None, bound=True):
    with env.sessions.begin() as db:
        content = Content(name=name, description="A 공정 직후 측정한 선폭", aliases=aliases or ["CD_MAIN"]).for_kind("term")
        db.add(KnowledgeItem(id=identity, kind="term", connection_key="a", published_version=1))
        db.add(KnowledgeVersion(item_id=identity, version=1, content=content, status="approved", created_by="admin"))
        if bound:
            binding = {"sources": [{"source_id": "a-src", "role": "measurement", "columns": ["result"]}],
                       "predicates": [], "expression": "", "notes": "승인된 측정 연결"}
            db.add(BindingHead(item_id=identity, definition_version=1, connection_key="a", published_version=1))
            db.add(BindingVersion(item_id=identity, definition_version=1, connection_key="a", version=1,
                content=binding, schema_snapshot=capture_binding(binding, catalog_snapshot(db)), status="approved", created_by="admin"))


def provider(ids=(), explanation=False, deferred=False):
    def complete(prompt, payload, schema):
        assert payload["resolve_terms"] is True
        assert "id" not in payload["dataset"]  # a tagged case, never a fake dataset
        value = extract_case(prompt, {**payload, "answers": payload["answers"][:1]}, schema)
        answer = payload["answers"][-1]
        value.mentions = [EvaluationMention(term="alpha", answer_id=answer["id"], quote=answer["text"],
            knowledge_ids=list(ids), explanation=answer["text"] if explanation else "", deferred=deferred)]
        return value
    return complete


def register_case(env, monkeypatch, *, project_id=None, text=REPORT, model=None):
    monkeypatch.setattr(llm, "complete", model or provider())
    r = env.client.post(BASE + "/evaluations", json={"project_id": project_id, "text": text})
    assert r.status_code == 202, r.text
    q = env.client.get(BASE + "/questions/" + r.json()["question_id"])
    assert q.status_code == 200, q.text
    return q.json()


def test_scope_only_registration_without_any_dataset_and_original_preservation(env, monkeypatch):
    with env.sessions.begin() as db:
        for row in db.scalars(select(DatasetVersion)):
            db.delete(row)
    q = register_case(env, monkeypatch)
    assert q["dataset"] is None and q["case_context"] == {"project_id": None}
    assert q["answers"][0]["text"] == REPORT and not q["dataset_changed"]
    assert q["status"] == "review" and "alpha" in q["followups"][0]
    assert q["interpretation"]["terms"][0]["status"] == "unmapped"
    assert not draft(env)["issues"], "an unavailable data mapping does not erase the reported observation"
    duplicate = env.client.post(BASE + "/evaluations", json={"project_id": None, "text": REPORT}).json()
    assert duplicate["status"] == "existing" and duplicate["question_id"] == q["id"]


def test_project_scope_validation_and_filters_without_dataset(env, monkeypatch):
    assert env.client.post(BASE + "/evaluations", json={"project_id": 999, "text": REPORT}).status_code == 422
    assert env.client.post(BASE + "/evaluations", json={"project_id": 17, "dataset_id": "a-data", "text": REPORT}).status_code == 422
    q = register_case(env, monkeypatch, project_id=17)
    assert env.client.get(BASE + "/questions?shared_only=true").json() == []
    assert env.client.get(BASE + "/questions?project_id=18").json() == []
    assert env.client.get(BASE + "/questions?project_id=17").json()[0]["id"] == q["id"]
    assert approve(env, draft(env)).status_code == 200
    assert len(cases(env, project_id=17)["cases"]) == 1
    assert cases(env, project_id=18)["cases"] == []
    monkeypatch.setattr(catalog, "projects_list", lambda: [])
    assert env.client.get(BASE + "/questions/" + q["id"]).status_code == 404


def test_approved_meaning_and_physical_binding_reused_and_rechecked(env, monkeypatch):
    definition(env, aliases=["alpha"])
    q = register_case(env, monkeypatch, model=provider(["width"]))
    link = q["interpretation"]["terms"][0]
    assert link["status"] == "linked" and link["references"][0]["version"] == 1 and link["bindings"]
    assert not q["followups"] and approve(env, draft(env)).status_code == 200
    found = cases(env, dataset_id="a-data")["cases"][0]
    assert found["dataset"] is None and any("용어" in reason for reason in found["match_reasons"])
    with env.sessions.begin() as db:
        db.get(BindingVersion, ("width", 1, "a", 1)).status = "retired"
    latest = env.client.get(BASE + "/questions/" + q["id"]).json()
    assert latest["interpretation"]["terms"][0]["status"] == "needs_review"
    assert not latest["interpretation"]["terms"][0]["bindings"]


def test_ambiguous_alias_asks_then_natural_answer_resolves_and_approves(env, monkeypatch):
    definition(env, aliases=["alpha"])
    definition(env, "other", "B 공정 선폭", aliases=["alpha"])
    q = register_case(env, monkeypatch, model=provider(["width"]))
    assert q["interpretation"]["terms"][0]["status"] == "ambiguous"
    assert "여러 후보" in q["followups"][0]
    old = draft(env)
    monkeypatch.setattr(llm, "complete", provider(["width"], explanation=True))
    response = env.client.post(BASE + f"/questions/{q['id']}/answers", json={
        "expected_revision": q["revision"], "text": "alpha는 A 공정 직후 선폭 CD_MAIN을 뜻해. B 공정은 아니야."})
    assert response.status_code == 202
    latest = env.client.get(BASE + "/questions/" + q["id"]).json()
    assert not latest["followups"] and len(latest["answers"]) == 2
    assert latest["interpretation"]["terms"][0]["status"] == "linked"
    assert approve(env, old).status_code == 409
    assert approve(env, draft(env)).status_code == 200
    assert env.client.get(BASE + "/questions/" + q["id"]).json()["status"] == "answered"


def test_explicit_unknown_answer_stops_repeated_question_without_claiming_mapping(env, monkeypatch):
    q = register_case(env, monkeypatch)
    monkeypatch.setattr(llm, "complete", provider(explanation=True, deferred=True))
    response = env.client.post(BASE + f"/questions/{q['id']}/answers", json={
        "expected_revision": q["revision"], "text": "alpha가 어떤 데이터인지는 지금 모르겠어. 나중에 확인할게."})
    assert response.status_code == 202
    latest = env.client.get(BASE + "/questions/" + q["id"]).json()
    assert latest["followups"] == [] and latest["interpretation"]["terms"][0]["status"] == "deferred"
    assert approve(env, draft(env)).status_code == 200
    assert any("미확인" in text for text in cases(env)["cases"][0]["limitations"])


def test_approved_case_alias_is_reused_only_in_its_scope(env, monkeypatch):
    definition(env)
    report = REPORT + " alpha는 A 공정 직후 선폭 CD_MAIN을 뜻해."
    q = register_case(env, monkeypatch, project_id=17, text=report, model=provider(["width"], explanation=True))
    assert approve(env, draft(env)).status_code == 200
    with env.sessions() as db:
        question = db.get(KnowledgeQuestion, q["id"])
        assert "alpha" in knowledge_candidates(db, question, [{"text": "alpha 비교"}])["width"]["aliases"]
        other = KnowledgeQuestion(kind="evaluation", dataset={"context_kind": "evaluation_case", "project_id": 18})
        assert "width" not in knowledge_candidates(db, other, [{"text": "alpha 비교"}])


@pytest.mark.parametrize("fault", ["fabricated_reference", "fabricated_quote", "missing_terms"])
def test_invalid_extraction_is_not_silently_registered_as_resolved(env, monkeypatch, fault):
    def bad(prompt, payload, schema):
        value = provider()(prompt, payload, schema)
        if fault == "fabricated_reference":
            value.mentions[0].knowledge_ids = ["not-approved"]
        elif fault == "fabricated_quote":
            value.mentions[0].quote = "This was not said"
        else:
            value.mentions = []
        return value
    q = register_case(env, monkeypatch, model=bad)
    assert q["status"] == "open" and q["answers"][0]["status"] == "error"
    assert q["answers"][0]["text"] == REPORT
    assert not env.client.get(BASE + "/drafts").json()


def test_passage_wire_keeps_observation_and_clarification_provenance_separate():
    payload = {"answers": [{"id": "original", "text": "alpha 증가."}, {"id": "reply", "text": "alpha는 CD_MAIN 선폭이야."}],
               "resolve_terms": True, "approved_knowledge": [{"id": "width"}], "case_context": {"project_id": 17}}
    wire, passages = prepare(payload)
    assert wire["approved_knowledge"] == [{"id": "width"}]
    ids = list(passages)
    result = restore(EvaluationPassages(name="평가", fields={"description": ids[0]}, mentions=[{
        "term": "alpha", "passage_id": ids[0], "explanation_passage_id": ids[1], "knowledge_ids": ["width"]}]), passages)
    assert result.fields["description"].answer_id == "original"
    assert result.mentions[0].answer_id == "reply" and result.mentions[0].explanation == payload["answers"][1]["text"]


def test_scope_case_continues_into_existing_analysis_result_review(setup, monkeypatch):
    from app.analytics import worker
    env = setup
    q = register_case(env, monkeypatch, text=REPORT + " alpha는 선폭을 뜻해.", model=provider(explanation=True))
    approval = approve(env, draft(env))
    assert approval.status_code == 200, approval.text
    env.reference = {"id": approval.json()["item_id"], "version": approval.json()["item_version"]}
    run = prepare_analysis(env, search={"query": "alpha 관련 사례", "dataset_id": "a-data"})
    assert execute(env, run).status_code == 202
    monkeypatch.setattr("app.evaluation_compute.compute", lambda cte, params, settings, columns, stored, checkpoint: fake_result(stored))
    assert worker.tick()
    current = env.client.get("/api/evaluation-analysis/" + run["id"]).json()
    assert current["status"] == "completed" and current["result"]["row_count"] == 4
    submitted = env.client.post("/api/evaluation-analysis/" + run["id"] + "/submit-review", json={
        "expected_revision": current["revision"], "workspace_token": current["workspace_token"], "note": "새 범위 사례에서 분석한 관측 검토"})
    assert submitted.status_code == 201, submitted.text
    review = draft(env)
    assert review["question"]["execution_evidence"]["run_id"] == run["id"]
    assert approve(env, review).status_code == 200
    assert len(cases(env, query="alpha 관련 사례", dataset_id="a-data")["cases"]) == 3


def test_rejection_feedback_and_last_questions_are_sent_with_natural_reply(env, monkeypatch):
    q = register_case(env, monkeypatch)
    first = draft(env)
    rejected = env.client.post(BASE + f"/drafts/{first['id']}/review", json={"expected_revision": first["revision"],
        "decision": "reject", "note": "alpha의 의미를 보완해 주세요."})
    assert rejected.status_code == 200
    current = env.client.get(BASE + "/questions/" + q["id"]).json()
    assert current["review_feedback"] == "alpha의 의미를 보완해 주세요."
    def complete(prompt, payload, schema):
        assert "alpha의 의미를 보완해 주세요." in payload["clarification_questions"]
        return provider(explanation=True, deferred=True)(prompt, payload, schema)
    monkeypatch.setattr(llm, "complete", complete)
    assert env.client.post(BASE + f"/questions/{q['id']}/answers", json={"expected_revision": current["revision"],
        "text": "alpha가 어떤 항목인지는 모르겠어."}).status_code == 202
    assert env.client.get(BASE + "/questions/" + q["id"]).json()["followups"] == []
