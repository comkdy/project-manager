"""Evaluation evidence stays distinct from calculations and survives safe replay."""
import asyncio
from copy import deepcopy
import json

from app import chat_workflow, evaluation_chat
from app.assistant import ChatRequest
from app.chat_contracts import OUTPUTS
from app import chat_history as history
from test_chat_history import env, start, finish


CASE = {"id": "case-a", "version": 2, "name": "공정 A 평가", "description": "alpha 증가와 parameter1 감소를 보고했다.",
    "details": {"operation": "oper A", "recipe_changes": "Recipe1, Recipe2, Recipe3 비교",
        "inline_observations": "CD alpha 증가", "epm_observations": "parameter1 감소",
        "conditions": "장비 EQ1, 2026년 9월", "evidence": "평가 보고서 A", "limitations": "장비별 분리 확인 필요"},
    "dataset": {"id": "epm", "name": "EPM", "version": 1, "project_id": None},
    "match_reasons": ["현재 데이터셋"], "limitations": ["현재 데이터 재검증 전"]}


def result(cases=None):
    return {"cases": deepcopy([CASE] if cases is None else cases), "warnings": [],
            "context": {"project_id": None, "dataset_id": "epm"}}


def test_evaluation_route_uses_only_retrieval_and_leaves_legacy_context_unloaded(monkeypatch):
    seen = []
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda search: seen.append(search) or result())
    request = ChatRequest(message="현재 데이터 관련 평가 사례로 추가 분석을 추천해줘", dataset_id="epm")
    class Adapter:
        async def run(self, skill, extra=None, validate=None):
            if skill == "route":
                return OUTPUTS["route"].validate_python({"kind": "evaluation_cases"}), None
            from test_evaluation_narrative import model_plan
            plan=OUTPUTS[skill].validate_python(model_plan(extra['evidence']))
            validate(plan)
            return plan,None
        async def load_context(self):
            raise AssertionError("Case suggestions must not run SQL/card context or query rows")
    async def collect():
        return [event async for event in chat_workflow.run_workflow(request, Adapter())]
    events = dict(asyncio.run(collect()))
    answer = events["evaluation_cases"]
    assert answer["status"] == "ready" and answer["cases"] == result()["cases"]
    assert answer["case_refs"] == [{"id": "case-a", "version": 2}]
    assert len(answer["suggestions"]) == 3
    assert "현재 데이터의 재검증 결과" in " ".join(answer["warnings"])
    assert all(s["case_ref"] == answer["case_refs"][0] for s in answer["suggestions"])
    assert seen == [evaluation_chat.search_request(request)] * 2
    assert answer['narrative']['status']=='ready'
    assert "proposal" not in events and "analysis" not in events


def test_case_search_preserves_selected_scope_without_treating_period_as_evaluation_dates():
    from types import SimpleNamespace
    card = SimpleNamespace(model_dump=lambda **_: {"dataset_id": "selected", "dataset_version": 4, "title": "CD",
        "filters": [{"column": "equipment", "op": "eq", "value": "EQ1"}], "period": {"mode": "off"},
        "python_script": "do not copy chart scripts", "x": "time", "y": "value"})
    request = SimpleNamespace(card=card, dataset_id=None, message="관련 사례", project_id=8,
        period_start=SimpleNamespace(model_dump=lambda **_: {"year": 2026, "week": 40}))
    search = evaluation_chat.search_request(request)
    assert search["dataset_id"] == "selected" and search["project_id"] == 8
    assert search["card"]["filters"][0]["value"] == "EQ1"
    assert search["card"]["dataset_version"] == 4
    assert search["card"]["period"] == {"mode": "off"}
    assert "python_script" not in search["card"]
    assert "start" not in search and search["period_start"] == {"year": 2026, "week": 40}


def test_missing_cases_do_not_generate_unsupported_facts_or_suggestions(monkeypatch):
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda _: result([]))
    value = evaluation_chat.evaluation_answer(ChatRequest(message="모르는 PARA 평가 사례", dataset_id="epm"))
    assert value["status"] == "empty" and not value["cases"] and not value["suggestions"]
    assert "찾지 못했습니다" in value["explanation"]


def test_suggestions_reference_reported_observations_and_keep_existing_deliverables():
    values = evaluation_chat.suggestions_for(CASE)
    assert "데이터셋" in values[0]["question"]
    assert "대시보드 카드" in values[1]["question"]
    assert "조회 SQL" in values[2]["question"]
    for item in values:
        assert "현재 결과가 아닙니다" in item["question"]
        assert "자동 적용하지 마세요" in item["question"]
        assert "CD alpha 증가" in item["question"] and "parameter1 감소" in item["question"]
        assert "Recipe3이 더 강한" not in item["question"]
        assert len(item["question"]) <= 6000
    inline_only = deepcopy(CASE)
    inline_only["details"]["epm_observations"] = ""
    assert len(evaluation_chat.suggestions_for(inline_only)) == 2


def test_approved_calculated_cases_keep_exact_result_and_offer_current_data_rechecks():
    computed = deepcopy(CASE)
    computed["details"].update(inline_observations="", epm_observations="", analysis_result="Recipe A 평균 2.5, Recipe B 평균 2.0. 평균 차이 -0.5.")
    computed["execution_evidence"] = {"run_id": "run-123", "method_version": "evaluation-dataset-v1",
        "captured_at": "2026-10-24T10:00:00Z", "summary": computed["details"]["analysis_result"]}
    answer = evaluation_chat.answer_from(result([computed]), {"query": "관련 사례"})
    assert len(answer["suggestions"]) == 3
    assert all(computed["details"]["analysis_result"] in s["question"] for s in answer["suggestions"])
    assert "이번 질문에서 다시 계산한 결과" in " ".join(answer["warnings"])
    assert "Inline·EPM" not in answer["suggestions"][2]["reason"]
    assert answer["cases"][0]["execution_evidence"]["run_id"] == "run-123"


def test_history_persists_references_and_revalidates_before_display_and_model_context(env, monkeypatch):
    current = result()
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda _: deepcopy(current))
    answer = evaluation_chat.evaluation_answer(ChatRequest(message="평가 사례 추천", dataset_id="epm"))
    c = start(env, dataset_id="epm")
    finish(c, "evaluation_cases", answer)
    with env.sessions() as db:
        stored = db.get(history.Turn, (c["id"], 1)).response
        assert set(stored) == {"kind", "search", "case_refs"}
        assert "CD alpha" not in json.dumps(stored, ensure_ascii=False)
    restored = env.client.get("/api/chat/conversations/" + c["id"]).json()["messages"][-1]
    assert restored["evaluation_cases"]["cases"][0]["version"] == 2
    current["cases"] = []
    withdrawn = env.client.get("/api/chat/conversations/" + c["id"]).json()["messages"][-1]
    assert withdrawn["evaluation_cases"]["status"] == "unavailable"
    assert "CD alpha" not in json.dumps(withdrawn, ensure_ascii=False)
    _, messages = history.begin_turn(ChatRequest(message="이어줘", dataset_id="epm", conversation_id=c["id"], conversation_revision=1), "owner")
    assert "CD alpha" not in json.dumps(messages, ensure_ascii=False)


def test_history_cannot_silently_switch_to_new_version_or_new_case(monkeypatch):
    previous = evaluation_chat.compact_evaluation_answer(evaluation_chat.answer_from(result(),
        {"query": "관련 사례", "project_id": None, "dataset_id": "epm", "card": None}))
    changed = deepcopy(CASE)
    changed["version"] = 3
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda _: result([changed]))
    restored = evaluation_chat.restore_evaluation_answer(previous, None)
    assert restored["status"] == "unavailable" and restored["cases"] == [] and restored["suggestions"] == []
    mismatch = evaluation_chat.restore_evaluation_answer(previous, 3)
    assert mismatch["status"] == "unavailable"


def test_history_retrieval_failure_hides_evidence_without_breaking_other_messages(monkeypatch):
    def fail(_):
        raise ValueError("source withdrawn")
    monkeypatch.setattr(evaluation_chat, "retrieve", fail)
    value = evaluation_chat.restore_evaluation_answer({"search": {"query": "사례", "project_id": None},
        "case_refs": [{"id": "case-a", "version": 2}]}, None)
    assert value["status"] == "unavailable" and not value["cases"]


def test_sse_persists_evaluation_event_as_completed(env, monkeypatch):
    from app import assistant
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda _: result())
    async def stream(request, adapter):
        yield "evaluation_cases", evaluation_chat.evaluation_answer(request)
    monkeypatch.setattr(assistant, "run_workflow", stream)
    response = env.client.post("/api/chat", json={"message": "평가 사례를 찾아줘", "dataset_id": "epm", "save_history": True})
    events = {block.splitlines()[0][7:]: json.loads(block.splitlines()[1][6:]) for block in response.text.strip().split("\n\n")}
    assert events["evaluation_cases"]["status"] == "ready"
    value = env.client.get("/api/chat/conversations/" + events["conversation"]["id"]).json()
    assert not value["running"] and value["messages"][-1]["status"] == "completed"
    assert value["messages"][-1]["evaluation_cases"]["cases"][0]["id"] == "case-a"


def test_execution_history_attaches_only_to_the_owned_original_case_turn(env, monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda _: result())
    c = start(env, project_id=7, dataset_id="epm")
    finish(c, "evaluation_cases", evaluation_chat.evaluation_answer(ChatRequest(message="사례", project_id=7, dataset_id="epm")))
    for user, project, reference, suggestion in [
        ("other", 7, {"id": "case-a", "version": 2}, "case-a:recheck"),
        ("owner", 8, {"id": "case-a", "version": 2}, "case-a:recheck"),
        ("owner", 7, {"id": "case-a", "version": 3}, "case-a:recheck"),
        ("owner", 7, {"id": "case-a", "version": 2}, "other:recheck"),
    ]:
        with env.sessions.begin() as db, pytest.raises(HTTPException):
            history.attach_evaluation_analysis(db, c["id"], 1, user, project, reference, suggestion, "run-a")
    with env.sessions.begin() as db:
        for _ in range(2):
            history.attach_evaluation_analysis(db, c["id"], 1, "owner", 7,
                {"id": "case-a", "version": 2}, "case-a:recheck", "run-a")
    with env.sessions() as db:
        response = db.get(history.Turn, (c["id"], 1)).response
        assert response["analysis_refs"] == [{"run_id": "run-a"}]
        assert db.get(history.Conversation, c["id"]).revision == 1
        assert "result" not in response


def test_execution_references_are_rechecked_when_reopening_history(env, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app import evaluation_analysis
    from app.models import User
    with env.sessions.begin() as db:
        User.__table__.create(db.connection())
        db.add(User(id="owner", username="owner", role="editor", active=True))
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda _: result())
    c = start(env, dataset_id="epm")
    finish(c, "evaluation_cases", evaluation_chat.evaluation_answer(ChatRequest(message="사례", dataset_id="epm")))
    with env.sessions.begin() as db:
        history.attach_evaluation_analysis(db, c["id"], 1, "owner", None,
            {"id": "case-a", "version": 2}, "case-a:recheck", "run-a")
    monkeypatch.setattr(evaluation_analysis, "checked_run", lambda *a: SimpleNamespace(status="completed", request={"project_id": None}))
    value = env.client.get("/api/chat/conversations/" + c["id"]).json()["messages"][-1]["evaluation_cases"]
    assert value["analyses"] == [{"id": "run-a", "status": "completed", "available": True}]
    def unavailable(*args):
        raise HTTPException(409, "stale dataset or retired seed")
    monkeypatch.setattr(evaluation_analysis, "checked_run", unavailable)
    stale = env.client.get("/api/chat/conversations/" + c["id"]).json()["messages"][-1]["evaluation_cases"]
    assert stale["analyses"][0]["available"] is False
    assert stale["analyses"][0]["status"] == "unavailable"
