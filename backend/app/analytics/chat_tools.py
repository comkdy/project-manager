"""Authenticated, bounded adapters for the existing analytics service.

No model-written SQL, raw rows, shared credentials, or model-generated results.
"""
import re
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .. import db as database
from .condition_evidence import date_supported, policy_supported
from .data import catalog
from .schemas import AnalysisRequest
from .service import create_run, find_run, run_json
from .worker import wake_worker

TOOL_DESCRIPTIONS = {
    "get_analysis_catalog": "현재 DB의 공개된 Inline/EPM PARA와 공정·recipe 이름 확인",
    "request_analysis": "명시된 조건을 검증해 실행 전 확인안 저장. 사용자 확인 후 기존 분석 API로 계산하거나 질문함에 등록",
    "get_analysis_result": "같은 DB와 원천 공개 범위를 재검증한 후 저장된 계산 결과 조회",
}
MAX_DISPLAY_SERIES = 20
CONDITION_FIELDS = ("start", "end", "baseline_start", "baseline_end", "current_start", "current_end",
                    "retest_policy", "min_samples", "absolute_delta")


def get_analysis_catalog():
    with database.SessionLocal() as db:
        return catalog(db)


def prepare_request(call, chat_request, available):
    values = call.model_dump(exclude={"kind", "analysis_kind", "goal", "evidence", "unresolved"})
    users = [m.content for m in chat_request.history if m.role == "user"] + [chat_request.message]
    blockers = set(call.unresolved)
    # Missing/unsupported conditions must never acquire implicit defaults from a model.
    for field in CONDITION_FIELDS:
        if values.get(field) is None:
            continue
        quote = call.evidence.get(field, "")
        supported = bool(quote and len(quote) <= 1000 and any(quote in message for message in users))
        if field in {"min_samples", "absolute_delta"}:
            numbers = re.findall(r"(?<![\w.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?", quote)
            supported = supported and any(float(n.replace(",", "")) == values[field] for n in numbers)
        if field.endswith(("start", "end")):
            supported = supported and any(quote in message and date_supported(values[field], quote, message) for message in users)
        elif field == "retest_policy":
            supported = supported and policy_supported(values[field], quote) and any(
                quote in message and policy_supported(values[field], message) for message in users)
        if not supported:
            values[field] = None
            if field.endswith(("start", "end")):
                blockers.add("period")
    selected = {k: values[k] for k in ("family", "parameter") if values.get(k)}
    if call.analysis_kind != "split" and values.get("operation"):
        selected["operation"] = values["operation"]
    if selected and not any(all(s.get(k) == v for k, v in selected.items()) for s in available["series"]):
        blockers.add("analysis_target")
    if call.analysis_kind == "split":
        matches = [s for s in available["recipe_operations"] if s["operation"] == values.get("operation")]
        if values.get("operation") and not matches:
            blockers.add("analysis_target")
        elif matches and any(values.get(k) and values[k] not in matches[0]["recipes"] for k in ("recipe_a", "recipe_b")):
            blockers.add("analysis_target")
        if selected and values["min_samples"] is not None and values["absolute_delta"] is not None:
            values["series_criteria"] = [{**s, "operation": s.get("operation") or "",
                "min_samples": values["min_samples"], "absolute_delta": values["absolute_delta"]}
                for s in available["series"] if all(s.get(k) == v for k, v in selected.items())]
            values["min_samples"] = values["absolute_delta"] = None
        values["family"], values["parameter"] = None, None
    if call.goal == "outlier":
        blockers.add("analysis_method")
    if call.analysis_kind == "statistics":
        values["retest_policy"] = values["min_samples"] = values["absolute_delta"] = None
        if call.retest_policy == "latest_per_site":
            blockers.add("analysis_method")
    try:
        request = AnalysisRequest(kind=call.analysis_kind, project_id=chat_request.project_id, **values)
    except ValidationError:
        # Keep a reviewable question rather than silently broadening the calculation.
        blockers.add("analysis_scope")
        request = AnalysisRequest(kind=call.analysis_kind, project_id=chat_request.project_id)
    return request, sorted(blockers), ["analysis_method"] if call.goal == "cause" else []


def request_analysis(call, chat_request, user_id, available):
    request, blockers, extra = prepare_request(call, chat_request, available)
    with database.SessionLocal.begin() as db:
        result = run_json(db, create_run(db, request, SimpleNamespace(id=user_id), blockers=blockers,
            extra_questions=extra, context_note=chat_request.message))
    if result["status"] == "queued":
        wake_worker()
    return result


def get_analysis_result(identity):
    with database.SessionLocal() as db:
        run = find_run(db, identity)
        if run.kind == "evaluation":
            raise HTTPException(404, "사례 기반 분석은 연결된 채팅에서 결과를 확인하세요.")
        return run_json(db, run)


def number(value):
    return "없음" if value is None else format(value, ".10g")


def answer(run, call, *, timings=None):
    result, request = run.get("result") or {}, run["request"]
    series = result.get("series", [])
    if call.analysis_kind == "split":
        series = [s for s in series if (not call.family or s["family"] == call.family)
                  and (not call.parameter or s["parameter"] == call.parameter)]
    shown = series[:MAX_DISPLAY_SERIES]
    scope = request.get("_project_scope")
    lines = [f"분석 범위: {scope['name'] + ' · lot_cd ' + ', '.join(scope['lot_cds']) if scope else '현재 DB의 등록 원천 전체'}."]
    if run["status"] in {"queued", "running"}:
        lines.append("분석이 계속 진행 중입니다. ‘분석 결과 열기’에서 진행 상태와 완료 결과를 확인할 수 있습니다.")
    elif run["status"] == "cancelling":
        lines.append("분석 취소 요청을 확인 중입니다. 새 결과는 반영하지 않습니다.")
    elif run["status"] == "cancelled":
        lines.append("분석이 취소되었습니다. 작업 상태 화면에서 다시 시도할 수 있습니다. 기존 질문·답변·승인은 유지됩니다.")
    elif run["status"] == "failed":
        lines.append(run["error"] or "분석을 완료하지 못했습니다.")
    elif not result:
        lines.append("계산에 필요한 조건이 정해지지 않아 분석을 실행하지 않았습니다. 확인할 내용을 AI 지식 질문함에 남겼습니다.")
    else:
        if not series:
            lines.append("지정한 조건에 해당하는 측정값이 없습니다. 데이터 범위와 대상을 확인해 주세요.")
        if run["kind"] == "trend":
            lines += [f"기준: {request['baseline_start']} 이상 ~ {request['baseline_end']} 미만",
                      f"비교: {request['current_start']} 이상 ~ {request['current_end']} 미만"]
        elif request.get("start"):
            lines.append(f"기간: {request['start']} 이상 ~ {request['end']} 미만")
        else:
            lines.append("기간: 저장된 전체 기간")
        if request.get("retest_policy"):
            lines.append("재측정: 각 기간·위치의 마지막 측정 (마지막 NULL 유지, 같은 시각은 record_id 순)" if request["retest_policy"] == "latest_per_site" else "재측정: 모든 측정 포함")
        for s in shown:
            name = " · ".join(v for v in (s["family"].upper(), s.get("operation"), s["parameter"]) if v)
            if run["kind"] == "statistics":
                v = s["statistics"]
                lines.append(f"{name}: 전체 {v['event_count']:,}건, 유효 {v['valid_count']:,}건, 결측 {v['missing_count']:,}건, 평균 {number(v['mean'])}, 최소 {number(v['min'])}, 최대 {number(v['max'])}.")
            else:
                a, b = (s["baseline"], s["current"]) if run["kind"] == "trend" else (s["group_a"], s["group_b"])
                label_a, label_b = ("기준", "비교") if run["kind"] == "trend" else (request["recipe_a"], request["recipe_b"])
                lines.append(f"{name}: {label_a} 평균 {number(a['mean'])} (유효 {a['valid_count']:,}건) → {label_b} 평균 {number(b['mean'])} (유효 {b['valid_count']:,}건). 평균 차이 ({label_b} − {label_a}) {number(s['delta'])}.")
                if s.get("status") == "needs_criteria":
                    lines.append("변화 판정 기준이 없어 변화 여부는 판정하지 않았습니다.")
                elif s.get("status") == "insufficient_data":
                    lines.append("지정한 최소 유효 측정 수를 충족하지 못했거나 평균을 계산할 수 없습니다.")
                elif s.get("status") in {"changed", "stable"}:
                    lines.append("지정한 평균 변화량 기준에 " + ("해당합니다." if s["status"] == "changed" else "미달합니다.") + " 통계적 유의성 판정은 아닙니다.")
        if len(series) > len(shown):
            lines.append(f"전체 {len(series):,}개 중 처음 {len(shown)}개를 표시했습니다. 전체 내용은 분석 결과에서 확인하세요.")
        if run["kind"] == "split":
            lines.append(f"Recipe 대응 공정: {request['operation']}. 각 측정 이전의 마지막 공정 이력으로 대응했습니다. 전체 Inline·EPM을 계산하고 요청한 PARA만 답변에 표시했습니다.")
            if result.get("excluded_ambiguous_wafer_count"):
                lines.append(f"공정 재방문·그룹 변경으로 모호한 wafer {result['excluded_ambiguous_wafer_count']}개를 제외한 임시 집계입니다. 처리 기준 확인이 필요합니다.")
        lines.extend(result.get("warnings", []))
    if call.goal in {"cause", "outlier"}:
        lines.append("현재 분석 도구는 원인이나 이상치를 확정하지 않습니다. 필요한 분석 방법·판정 기준을 질문함에 남겼습니다.")
    if run.get("questions"):
        lines.append("질문함 확인: " + " / ".join(q["title"] for q in run["questions"]))
    return {"kind": "analysis", "run_id": run["id"], "status": run["status"], "explanation": "\n".join(lines),
            "goal": call.goal,
            "request": request, "questions": run.get("questions", []), "series": [{k: v for k, v in s.items() if k != "points"} for s in shown],
            "display": {"family": call.family, "parameter": call.parameter, "shown": len(shown), "total": len(series)},
            "source_watermark": run.get("source_watermark"), "method_version": run.get("method_version"), "timings": timings or {}}


def restore_answer(response):
    """Recheck source visibility on every history read; refresh background jobs."""
    try:
        run = get_analysis_result(response["run_id"])
    except HTTPException:
        return {"kind": "analysis", "explanation": "분석 원천의 공개 범위 또는 프로젝트가 변경되어 이전 결과를 표시할 수 없습니다.", "status": "unavailable"}
    if response["status"] in {"queued", "running", "cancelling"} or run['status'] in {'cancelling', 'cancelled'}:
        return answer(run, SimpleNamespace(analysis_kind=run["kind"], goal=response.get("goal", "describe"),
            family=response.get("display", {}).get("family"), parameter=response.get("display", {}).get("parameter")))
    return response


async def execute_analysis(call, adapter):
    yield "status", {"message": "분석 Tool에서 등록된 PARA·공정·recipe와 요청 조건을 확인하고 있습니다."}
    available = await run_in_threadpool(get_analysis_catalog)
    if not available["supported"]:
        raise HTTPException(422, "현재 DB에 분석 가능한 Inline/EPM 원천이 없습니다. 데이터 관리를 확인하세요.")
    from .proposals import create_proposal
    effective = getattr(adapter, 'analysis_request', adapter.request)
    if hasattr(adapter, 'analysis_request'):
        from ..catalog import projects_list
        from ..chat_grounding import ground_analysis
        projects = await run_in_threadpool(projects_list)
        call, current = await run_in_threadpool(
            ground_analysis, call, adapter.request, {**available, 'projects': projects})
        if current.project_id != effective.project_id:
            raise HTTPException(409, '질문을 해석하는 동안 프로젝트가 변경되었습니다. 다시 요청해 주세요.')
        effective = current
    proposal = await run_in_threadpool(create_proposal, call, effective, adapter.actor_id, available,
        getattr(adapter, "conversation_id", None), getattr(adapter, "turn_sequence", None),
        origin_project_id=adapter.request.project_id)
    yield "analysis_proposal", proposal
