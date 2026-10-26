"""Analytics tool boundaries, real metadata transactions and deterministic answers."""
import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app import chat_workflow, db as database
from app.assistant import ChatRequest
from app.analytics import chat_tools as tools, service
from app.analytics.chat_contracts import AnalysisCall
from app.analytics.models import AnalysisProposal, AnalysisRun
from app.models import AppProject
from app.semantic.collector_models import KnowledgeQuestion
from test_analytics_service import env, measurement, trend_request


CATALOG = {"supported": True, "series": [{"family": "epm", "operation": "", "parameter": "PARA1"}],
           "recipe_operations": [{"operation": "oper A", "recipes": ["RCP1", "RCP2"]}]}
TEXT = "2026-10-01T00:00:00Z부터 2026-10-03T00:00:00Z 전, 2026-10-03T00:00:00Z부터 2026-10-05T00:00:00Z 전을 비교. 마지막 측정만 사용. 최소 1건 평균 차이 2."


def call(**overrides):
    values = trend_request()
    values.pop("kind")
    values.update(evidence={k: TEXT for k in tools.CONDITION_FIELDS})
    return AnalysisCall(kind="analysis", analysis_kind="trend", **(values | overrides))


def chat(message=TEXT, **overrides):
    return ChatRequest(message=message, **overrides)


def test_explicit_conditions_reuse_existing_api_without_defaults():
    request, blockers, extra = tools.prepare_request(call(), chat(), CATALOG)
    assert request.model_dump(mode="json") | {} == service.AnalysisRequest(**trend_request()).model_dump(mode="json")
    assert not blockers and not extra


def test_unsupported_model_defaults_are_discarded():
    request, blockers, _ = tools.prepare_request(call(), chat("최근 추세를 알려줘"), CATALOG)
    assert request.baseline_start is None and request.retest_policy is None
    assert request.min_samples is request.absolute_delta is None
    assert "period" in blockers


def test_timezone_is_not_invented():
    text = "2026-10-01부터 2026-10-03, 2026-10-03부터 2026-10-05"
    request, blockers, _ = tools.prepare_request(call(evidence={k: text for k in tools.CONDITION_FIELDS}), chat(text), CATALOG)
    assert request.baseline_start is None and "period" in blockers


def test_numeric_criteria_must_be_present_in_user_evidence():
    request, _, _ = tools.prepare_request(call(min_samples=999, absolute_delta=888), chat(), CATALOG)
    assert request.min_samples is request.absolute_delta is None


@pytest.mark.parametrize("field,value", [("parameter", "PARA999"), ("operation", "oper1"), ("recipe_b", "RCP999")])
def test_unknown_target_is_not_aliased(field, value):
    plan = AnalysisCall(kind="analysis", analysis_kind="split", family="epm", parameter="PARA1", operation="oper A", recipe_a="RCP1", recipe_b="RCP2")
    _, blockers, _ = tools.prepare_request(plan.model_copy(update={field: value}), chat(), CATALOG)
    assert "analysis_target" in blockers


def test_missing_conditions_create_actual_inbox_questions_and_no_job(env):
    plan = AnalysisCall(kind="analysis", analysis_kind="trend", goal="cause", family="epm", parameter="PARA1")
    run = tools.request_analysis(plan, chat("최근 감소 원인"), env.actor.id, CATALOG)
    assert run["status"] == "needs_input" and not run["result"]
    assert len(run["questions"]) == 4
    with env.sessions() as db:
        assert {q.subject for q in db.scalars(select(KnowledgeQuestion))} == {"analysis:period", "analysis:retest_policy", "analysis:change_criteria", "analysis:analysis_method"}


def test_outlier_without_method_never_computes(env):
    plan = AnalysisCall(kind="analysis", analysis_kind="statistics", goal="outlier")
    run = tools.request_analysis(plan, chat("이상치를 찾아줘"), env.actor.id, CATALOG)
    assert run["status"] == "needs_input"
    assert "원인이나 이상치를 확정하지" in tools.answer(run, plan)["explanation"]


def test_live_actor_permission_rechecked(env):
    env.actor.id = "reader"
    with pytest.raises(HTTPException) as error:
        tools.request_analysis(call(), chat(), env.actor.id, CATALOG)
    assert error.value.status_code == 403


def test_tool_result_database_boundary(env):
    run = tools.request_analysis(call(), chat(), env.actor.id, CATALOG)
    with database.use_source(database.SourceBinding(None, "b")):
        with pytest.raises(HTTPException) as error:
            tools.get_analysis_result(run["id"])
    assert error.value.status_code == 404


def test_worker_result_is_the_answer_not_model_math(env, monkeypatch):
    run = tools.request_analysis(call(min_samples=None, absolute_delta=None), chat(), env.actor.id, CATALOG)
    with env.sessions.begin() as db:
        db.get(AnalysisRun, run["id"]).status = "running"
    monkeypatch.setattr(service, "extract", lambda *a: ([measurement(value=10), measurement(value=7, at="2026-10-04T00:00:00Z")], [], {"captured_at": "now"}))
    service.process_run(run["id"])
    result = tools.answer(tools.get_analysis_result(run["id"]), call())
    assert result["series"][0]["delta"] == -3
    assert "평균 차이 (비교 − 기준) -3" in result["explanation"]
    assert "변화 여부는 판정하지" in result["explanation"]
    assert "points" not in result["series"][0]


def test_project_scope_is_server_resolved_and_rechecked(env, monkeypatch):
    from app.models import ProjectRemoval
    AppProject.__table__.create(env.sessions.kw["bind"])
    ProjectRemoval.__table__.create(env.sessions.kw["bind"])
    with env.sessions.begin() as db:
        db.add(AppProject(id=5, connection_key="a", name="Project A", lot_cds=["XXXA"], active=True))
    run = tools.request_analysis(call(), chat(project_id=5), env.actor.id, CATALOG)
    assert run["request"]["_project_scope"]["lot_cds"] == ["XXXA"]
    captured = []
    def extract(snapshot, request, *, project_lot_cds):
        captured.append(project_lot_cds)
        return [], [], {}
    monkeypatch.setattr(service, "extract", extract)
    with env.sessions.begin() as db:
        db.get(AnalysisRun, run["id"]).status = "running"
    service.process_run(run["id"])
    assert captured == [["XXXA"]]
    with env.sessions.begin() as db:
        db.get(AppProject, 5).lot_cds = ["OTHER"]
    with pytest.raises(HTTPException) as error:
        tools.get_analysis_result(run["id"])
    assert error.value.status_code == 409


def test_analysis_routing_uses_one_model_call(monkeypatch):
    calls = []
    class Adapter:
        async def run(self, stage):
            calls.append(stage)
            return call(), None
    async def execute(plan, adapter):
        yield "analysis", {"run_id": "run", "explanation": "computed"}
    monkeypatch.setattr(tools, "execute_analysis", execute)
    async def collect():
        return [item async for item in chat_workflow.run_workflow(chat(), Adapter())]
    events = asyncio.run(collect())
    assert calls == ["route"] and events[-1][0] == "analysis"


def test_analysis_chat_proposes_conditions_without_starting_a_run(env, monkeypatch):
    monkeypatch.setattr(tools, "get_analysis_catalog", lambda: CATALOG)
    async def collect():
        return [item async for item in tools.execute_analysis(call(), SimpleNamespace(request=chat(), actor_id=env.actor.id))]
    event, result = asyncio.run(collect())[-1]
    assert event == "analysis_proposal" and result["status"] == "pending" and result["run_id"] is None
    with env.sessions() as db:
        assert db.get(AnalysisProposal, result["proposal_id"]) is not None
        assert not list(db.scalars(select(AnalysisRun)))
        assert not list(db.scalars(select(KnowledgeQuestion)))


def test_history_visibility_check_masks_old_results(monkeypatch):
    monkeypatch.setattr(tools, "get_analysis_result", lambda _: (_ for _ in ()).throw(HTTPException(404)))
    response = tools.restore_answer({"run_id": "hidden", "status": "completed", "explanation": "private numbers"})
    assert response["status"] == "unavailable" and "private numbers" not in str(response)


def test_grounded_project_disappearing_before_proposal_is_not_broadened(env, monkeypatch):
    from app import catalog
    monkeypatch.setattr(tools, 'get_analysis_catalog', lambda: CATALOG)
    monkeypatch.setattr(catalog, 'projects_list', lambda: [])
    original = chat('XXXA에서 oper A의 특성 비교')
    adapter = SimpleNamespace(request=original, analysis_request=original.model_copy(update={'project_id': 5}), actor_id=env.actor.id)
    async def collect():
        return [event async for event in tools.execute_analysis(
            AnalysisCall(kind='analysis', analysis_kind='split', operation='oper A'), adapter)]
    with pytest.raises(HTTPException) as error:
        asyncio.run(collect())
    assert error.value.status_code == 409
    with env.sessions() as db:
        assert not list(db.scalars(select(AnalysisProposal)))


def test_grounded_global_chat_keeps_target_project_and_origin_separate(env, monkeypatch):
    from app import catalog
    from app.models import ProjectRemoval
    AppProject.__table__.create(env.sessions.kw['bind'], checkfirst=True)
    ProjectRemoval.__table__.create(env.sessions.kw['bind'], checkfirst=True)
    with env.sessions.begin() as db:
        db.add(AppProject(id=5, connection_key='a', name='XXXA', lot_cds=['XXXA'], active=True))
    monkeypatch.setattr(tools, 'get_analysis_catalog', lambda: CATALOG)
    monkeypatch.setattr(catalog, 'projects_list', lambda: [{'id':5, 'name':'XXXA', 'lot_cds':['XXXA']}])
    original = chat('XXXA에서 operA 에 따른 특성 변화가 관측돼?')
    adapter = SimpleNamespace(request=original, analysis_request=original.model_copy(update={'project_id':5}), actor_id=env.actor.id)
    async def collect():
        return [event async for event in tools.execute_analysis(
            AnalysisCall(kind='analysis', analysis_kind='split', operation='operA'), adapter)]
    event, result = asyncio.run(collect())[-1]
    assert event == 'analysis_proposal' and result['project_id'] == 5
    assert result['request']['operation'] == 'oper A'
    assert result['explanation'].startswith('해석한 범위: XXXA · 공정 oper A.')
    with env.sessions() as db:
        assert db.get(AnalysisProposal, result['proposal_id']).context['origin_project_id'] is None


def test_split_threshold_only_applies_to_requested_series():
    plan = AnalysisCall(kind="analysis", analysis_kind="split", family="epm", parameter="PARA1",
                        operation="oper A", recipe_a="RCP1", recipe_b="RCP2", min_samples=1, absolute_delta=2,
                        evidence={"min_samples": TEXT, "absolute_delta": TEXT})
    request, _, _ = tools.prepare_request(plan, chat(), CATALOG)
    assert request.min_samples is request.absolute_delta is None
    assert len(request.series_criteria) == 1 and request.series_criteria[0].parameter == "PARA1"


def test_empty_model_content_retries_once_and_openrouter_options_are_scoped():
    import httpx
    from app.chat_workflow import ModelAdapter
    import json
    observed = []
    def transport(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": None if len(observed) == 1 else '{"kind":"analysis","analysis_kind":"trend"}'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter = ModelAdapter(client, "https://openrouter.ai/api/v1/chat/completions", "test", {}, chat(), {})
            return await adapter.run("route")
    assert asyncio.run(run())[0].kind == "analysis"
    assert len(observed) == 2
    assert observed[0]["response_format"] == {"type": "json_object"}
    assert observed[0]["reasoning"] == {"effort": "minimal"}
