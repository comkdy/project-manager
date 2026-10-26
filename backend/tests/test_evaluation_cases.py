"""Evaluation cases use isolated SQLite and a grounded fake provider."""
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import db as database
from app.models import DatasetVersion, Source
from app.semantic import collector_config as llm
from app.semantic.collector_models import CollectedDraft, KnowledgeAnswer, KnowledgeQuestion
from app.semantic.collector_schemas import ExtractedKnowledge
from app.semantic.evaluations import retrieve_evaluation_cases
from app.semantic.models import KnowledgeItem, KnowledgeVersion
from app.semantic.schemas import ContextRequest
from app.semantic.service import catalog_snapshot, get_semantic_context
from test_semantic_collection import BASE, approve, draft, env


REPORT = "Operation A에서 Recipe1, Recipe2, Recipe3 비교. Inline CD alpha 증가. EPM parameter1 감소."


def extract_case(prompt, payload, schema):
    assert "kind=evaluation" in prompt
    answer = payload["answers"][-1]
    text = answer["text"]
    fields = {"description": text}
    for key, value in {"operation": "Operation A", "recipe_changes": "Recipe1, Recipe2, Recipe3 비교",
                       "inline_observations": "Inline CD alpha 증가", "epm_observations": "EPM parameter1 감소"}.items():
        if value in text:
            fields["details." + key] = value
    return ExtractedKnowledge(name="공정 A 평가 사례", fields={key: {
        "value": value, "quote": value, "answer_id": answer["id"]} for key, value in fields.items()})


def register(env, monkeypatch, text=REPORT, dataset_id="a-data"):
    monkeypatch.setattr(llm, "complete", extract_case)
    response = env.client.post(BASE + "/evaluations", json={"dataset_id": dataset_id, "text": text})
    assert response.status_code == 202, response.text
    return response.json()


def publish(env, monkeypatch, **kwargs):
    receipt = register(env, monkeypatch, **kwargs)
    d = draft(env)
    response = approve(env, d)
    assert response.status_code == 200, response.text
    return receipt, response.json()


def cases(env, query="alpha와 parameter1 관련 평가 사례", **kwargs):
    with env.sessions() as db:
        return retrieve_evaluation_cases(db, query, **kwargs)


def test_natural_registration_review_and_retrieval_preserve_original(env, monkeypatch):
    env.actor.id, env.actor.role = "reader", "viewer"
    assert [d["id"] for d in env.client.get(BASE + "/evaluation-datasets").json()] == ["a-data"]
    receipt = register(env, monkeypatch)
    assert cases(env, dataset_id="a-data")["cases"] == []
    detail = env.client.get(BASE + f"/questions/{receipt['question_id']}").json()
    assert detail["answers"][0]["text"] == REPORT and detail["kind"] == "evaluation"
    assert detail["status"] == "review"
    env.actor.id, env.actor.role = "admin", "admin"
    d = draft(env)
    assert not d["issues"]  # Optional historical conditions need not block recording.
    assert d["evidence"]["details.operation"]["quote"] == "Operation A"
    env.actor.id, env.actor.role = "reader", "viewer"
    assert approve(env, d).status_code == 403
    env.actor.id, env.actor.role = "admin", "admin"
    approved = approve(env, d).json()
    found = cases(env, dataset_id="a-data")["cases"]
    assert len(found) == 1 and found[0]["id"] == approved["item_id"]
    assert found[0]["details"]["epm_observations"] == "EPM parameter1 감소"
    assert found[0]["question_id"] == receipt["question_id"]
    assert any("현재 데이터에서 재계산" in warning for warning in found[0]["limitations"])
    env.actor.id, env.actor.role = "reader", "viewer"
    items = env.client.get("/api/semantic/items").json()["items"]
    assert items[0]["evaluation_context"]["dataset"]["id"] == "a-data"
    assert items[0]["evaluation_context"]["question_id"] == receipt["question_id"]


def test_same_report_is_not_another_independent_case(env, monkeypatch):
    first = register(env, monkeypatch)
    second = register(env, monkeypatch)
    assert first["question_id"] == second["question_id"] and second["status"] == "existing"
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 1
        assert db.scalar(select(func.count()).select_from(KnowledgeAnswer)) == 1
        assert db.scalar(select(func.count()).select_from(CollectedDraft)) == 1


def test_missing_observations_followup_answer_supersedes_draft(env, monkeypatch):
    receipt = register(env, monkeypatch, text="Operation A에서 Recipe1, Recipe2, Recipe3 비교.")
    first = draft(env)
    assert approve(env, first).status_code == 422
    q = env.client.get(BASE + f"/questions/{receipt['question_id']}").json()
    assert any("관측" in value for value in q["followups"])
    response = env.client.post(BASE + f"/questions/{q['id']}/answers", json={"expected_revision": q["revision"], "text": REPORT})
    assert response.status_code == 202, response.text
    second = draft(env)
    assert second["id"] != first["id"]
    assert approve(env, first).status_code == 409
    assert approve(env, second).status_code == 200
    assert len(cases(env, dataset_id="a-data")["cases"]) == 1


def test_reviewer_resolving_missing_content_updates_the_same_request_action(env, monkeypatch):
    receipt = register(env, monkeypatch, text="Operation A에서 Recipe1, Recipe2, Recipe3 비교.")
    first = draft(env)
    qpath = BASE + f"/questions/{receipt['question_id']}"
    assert env.client.get(qpath).json()["followups"]
    proposal = first["proposal"]
    proposal["content"]["details"]["inline_observations"] = "담당자 확인: Inline CD alpha 증가"
    proposal["content"]["unresolved"] = []
    saved = env.client.post(BASE + f"/drafts/{first['id']}", json={
        "expected_revision": first["revision"], "proposal": proposal, "note": "원문 담당자와 관측을 확인"})
    assert saved.status_code == 200
    current = env.client.get(qpath).json()
    assert current["id"] == receipt["question_id"] and current["status"] == "review"
    assert current["followups"] == [] and saved.json()["followups"] == []
    assert approve(env, saved.json()).status_code == 200
    assert env.client.get(qpath).json()["status"] == "answered"


def test_invented_summary_with_real_quote_cannot_be_approved(env, monkeypatch):
    def bad_model(prompt, payload, schema):
        value = extract_case(prompt, payload, schema)
        value.fields["details.epm_observations"].value = "Recipe 변경이 EPM의 확정 원인이다."
        return value
    monkeypatch.setattr(llm, "complete", bad_model)
    response = env.client.post(BASE + "/evaluations", json={"dataset_id": "a-data", "text": REPORT})
    assert response.status_code == 202
    d = draft(env)
    assert not d["proposal"]["content"]["details"].get("epm_observations")
    assert d["issues"] and approve(env, d).status_code == 422


def test_provider_failure_keeps_text_and_existing_retry_works(env, monkeypatch):
    def fail(*args):
        raise RuntimeError("hidden-provider-data")
    monkeypatch.setattr(llm, "complete", fail)
    response = env.client.post(BASE + "/evaluations", json={"dataset_id": "a-data", "text": REPORT})
    receipt = response.json()
    q = env.client.get(BASE + f"/questions/{receipt['question_id']}")
    assert q.json()["answers"][0]["text"] == REPORT
    assert q.json()["answers"][0]["status"] == "error" and "hidden-provider-data" not in q.text
    monkeypatch.setattr(llm, "complete", extract_case)
    assert env.client.post(BASE + f"/answers/{receipt['answer_id']}/retry").status_code == 202
    assert approve(env, draft(env)).status_code == 200


def test_case_scope_database_project_hidden_and_stale(env, monkeypatch):
    with env.sessions.begin() as db:
        db.get(DatasetVersion, ("a-data", 1)).project_id = 17
    publish(env, monkeypatch)
    assert cases(env, project_id=18)["cases"] == []
    assert cases(env)["cases"] == []
    assert len(cases(env, project_id=17, dataset_id="a-data")["cases"]) == 1
    with database.use_source(database.SourceBinding(None, "b", ignored_tables=())):
        assert cases(env, project_id=17)["cases"] == []
    with env.sessions.begin() as db:
        db.get(Source, "a-src").available = False
    assert cases(env, project_id=17)["cases"] == []
    assert env.client.get("/api/semantic/items").json()["items"] == []
    with env.sessions.begin() as db:
        db.get(Source, "a-src").available = True
        db.add(DatasetVersion(id="a-data", version=2, name="변경된 측정", project_id=17,
            spec={"source_id": "a-src", "joins": []}, columns=[{"name": "id", "type": "integer"}]))
    assert cases(env, project_id=17, dataset_id="a-data")["cases"] == []
    item = env.client.get("/api/semantic/items").json()["items"][0]
    assert item["evaluation_context"]["dataset_changed"] is True
    assert item["evaluation_context"]["current_dataset_version"] == 2


def test_related_dataset_and_explicit_card_filter(env, monkeypatch):
    publish(env, monkeypatch)
    with env.sessions.begin() as db:
        db.add(DatasetVersion(id="derived", version=1, name="측정 상세", spec={"source_id": "a-src", "joins": []},
                              columns=[{"name": "id", "type": "integer"}]))
    related = cases(env, dataset_id="derived")["cases"]
    assert len(related) == 1 and any("원천" in reason for reason in related[0]["match_reasons"])
    broad = cases(env, query="관련 사례를 찾아줘", dataset_id="derived")["cases"]
    assert len(broad) == 1 and any("일치하는지는 확인되지" in warning for warning in broad[0]["limitations"])
    assert cases(env, query="아무런 관계없는 beta99", dataset_id="derived")["cases"] == []
    card = {"dataset_id": "a-data", "dataset_version": 1,
            "filters": [{"column": "parameter", "value": "parameter99"}]}
    assert cases(env, query="관련 사례", card=card)["cases"] == []
    card["filters"][0]["value"] = "parameter1"
    assert len(cases(env, query="관련 사례", card=card)["cases"]) == 1
    card["filters"] = [{"column": "operation", "op": "eq", "value": "A"},
                       {"column": "parameter", "op": "eq", "value": "parameter99"}]
    assert cases(env, query="관련 사례", card=card)["cases"] == []
    card["filters"] = [{"column": "parameter", "op": "ne", "value": "parameter1"}]
    assert cases(env, query="관련 사례", card=card)["cases"] == []
    card["dataset_version"] = 2
    with pytest.raises(HTTPException) as error:
        cases(env, card=card)
    assert error.value.status_code == 409


def test_korean_particle_search_and_explicit_unknown_para(env, monkeypatch):
    publish(env, monkeypatch, text=REPORT + " 선폭 변화가 확인됐다.")
    found = cases(env, query="선폭이 달라진 평가 사례")["cases"]
    assert len(found) == 1 and any("선폭" in reason for reason in found[0]["match_reasons"])
    assert cases(env, query="PARA999 관련 평가 사례", dataset_id="a-data")["cases"] == []
    assert cases(env, query="parameter999는 어떤 사례가 있어?", dataset_id="a-data")["cases"] == []
    assert len(cases(env, query="parameter1이 변한 사례", dataset_id="a-data")["cases"]) == 1
    assert len(cases(env, query="확인할 항목은 parameter1.", dataset_id="a-data")["cases"]) == 1


def test_named_operation_without_dataset_and_single_character_card_filter(env, monkeypatch):
    publish(env, monkeypatch)
    found = cases(env, query="operation A 평가 사례를 찾아줘")["cases"]
    assert len(found) == 1 and "평가 공정 일치: Operation A" in found[0]["match_reasons"]
    assert cases(env, query="operation B 평가 사례를 찾아줘")["cases"] == []
    card = {"dataset_id": "a-data", "dataset_version": 1,
            "filters": [{"column": "operation", "op": "eq", "value": "A"}]}
    assert len(cases(env, query="관련 사례", card=card)["cases"]) == 1
    card["filters"][0]["value"] = "B"
    assert cases(env, query="관련 사례", card=card)["cases"] == []


def test_edit_version_keeps_origin_and_retirement_removes_case(env, monkeypatch):
    receipt, approved = publish(env, monkeypatch)
    item = env.client.get("/api/semantic/items").json()["items"][0]
    content = item["content"]
    content["details"]["limitations"] = "동일 평가 조건에서만 참고"
    changed = env.client.post(f"/api/semantic/items/{approved['item_id']}/versions", json={
        "expected_revision": item["revision"], "content": content}).json()
    assert cases(env, dataset_id="a-data")["cases"][0]["version"] == 1
    reviewed = env.client.post(f"/api/semantic/items/{approved['item_id']}/review", json={
        "expected_revision": changed["revision"], "version": 2, "decision": "approve", "note": "한계 보완"})
    assert reviewed.status_code == 200, reviewed.text
    current = cases(env, dataset_id="a-data")["cases"][0]
    assert current["version"] == 2 and current["question_id"] == receipt["question_id"]
    retired = env.client.post(f"/api/semantic/items/{approved['item_id']}/review", json={
        "expected_revision": reviewed.json()["revision"], "version": 2, "decision": "retire", "note": "재검토"})
    assert retired.status_code == 200
    assert cases(env, dataset_id="a-data")["cases"] == []


def test_evaluation_does_not_block_existing_semantic_resolution(env, monkeypatch):
    publish(env, monkeypatch)
    with env.sessions.begin() as db:
        request = ContextRequest(query="alpha parameter1", connection_key="a", workspace_token=database.workspace_token())
        result = get_semantic_context(db, request, env.actor, catalog=catalog_snapshot(db))
        assert result["items"] == [] and not result["blocking_gaps"] and not result["clarifications"]
    item = env.client.get("/api/semantic/items").json()["items"][0]
    assert env.client.post("/api/semantic/items", json={"kind": "evaluation", "scope": "database", "content": item["content"]}).status_code == 422


def test_dataset_change_before_review_blocks_approval(env, monkeypatch):
    register(env, monkeypatch)
    d = draft(env)
    with env.sessions.begin() as db:
        db.get(DatasetVersion, ("a-data", 1)).name = "수정된 데이터셋"
    assert approve(env, d).status_code == 422


def test_case_name_does_not_block_existing_business_definition(env, monkeypatch):
    publish(env, monkeypatch)
    def extract_term(prompt, payload, schema):
        answer = payload["answers"][-1]
        return ExtractedKnowledge(name="공정 A 평가 사례", fields={"description": {
            "value": answer["text"], "quote": answer["text"], "answer_id": answer["id"]}})
    monkeypatch.setattr(llm, "complete", extract_term)
    q = env.client.post(BASE + "/questions", json={"dataset_id": "a-data", "title": "기존 용어를 설명해 주세요.",
        "reason": "업무 정의", "kind": "term", "topic": "other", "subject": "기존 용어"}).json()
    response = env.client.post(BASE + f"/questions/{q['id']}/answers", json={
        "expected_revision": q["revision"], "text": "공정 A 평가 사례는 검토 결과 문서의 이름입니다."})
    assert response.status_code == 202
    d = draft(env)
    assert d["conflicts"] == []
    assert approve(env, d).status_code == 200


def test_changed_dataset_can_reregister_report_for_fresh_review(env, monkeypatch):
    first, approved = publish(env, monkeypatch)
    with env.sessions.begin() as db:
        db.add(DatasetVersion(id="a-data", version=2, name="새 측정", spec={"source_id": "a-src", "joins": []},
                              columns=[{"name": "id", "type": "integer"}]))
    second = register(env, monkeypatch)
    assert second["question_id"] != first["question_id"] and second["status"] == "queued"
    assert cases(env, dataset_id="a-data")["cases"] == []
    d = draft(env)
    with env.sessions() as db:
        revision = db.get(KnowledgeItem, approved["item_id"]).revision
    response = approve(env, d, target_item_id=approved["item_id"], target_revision=revision)
    assert response.status_code == 200, response.text
    found = cases(env, dataset_id="a-data")["cases"]
    assert len(found) == 1 and found[0]["dataset"]["version"] == 2
    assert found[0]["id"] == approved["item_id"] and found[0]["version"] == 2
