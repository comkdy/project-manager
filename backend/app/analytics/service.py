import json
from datetime import timedelta
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import func, select, text

from .. import db as database
from ..models import Audit, User, utcnow
from ..semantic.collector_models import CollectedDraft, KnowledgeQuestion
from ..semantic.collector_service import approved_knowledge, inventory, question_visible
from ..semantic.service import digest
from .data import extract, selected_snapshot, source_snapshot, validate_snapshot
from .models import AnalysisRun
from .schemas import AnalysisRequest
from .cancellation import AnalysisCancelled, checking
from ..job_controls import analysis_controls

METHOD_VERSION = "descriptive-parameters-v1"
QUESTION_TEXT = {
    "period": ("분석 기준 기간과 비교 기간은 언제인가요?", "시작·종료 시각과 시간대를 확인해야 하며, '최근'의 기간을 임의로 정하지 않습니다."),
    "retest_policy": ("같은 위치의 재측정은 마지막 측정만 사용할까요, 모든 측정을 사용할까요?", "마지막 값을 선택하면 마지막 NULL도 보존합니다. 모든 측정은 반복 측정 횟수만큼 가중됩니다."),
    "change_criteria": ("PARA별 변화 판정에 사용할 최소 유효 측정 수와 평균 변화량 기준은 무엇인가요?", "단위가 다른 PARA의 기준을 구분해 주세요. 규격·불량 여부·통계적 유의성을 임의로 추정하지 않습니다."),
    "split_groups": ("어느 공정의 어떤 두 recipe와 lot/wafer를 비교할까요?", "평가군·비교군과 기간을 확인해야 합니다. 현재 구현은 명시한 recipe 그룹의 관찰 비교이며 실험의 인과효과를 확정하지 않습니다."),
    "recipe_crossover": ("비교 기간 중 recipe 그룹이 바뀐 wafer는 어떻게 비교해야 하나요?", "공정 재방문·재작업의 대응 규칙이 필요합니다. 두 그룹에 걸친 wafer를 임의로 한쪽에 배정할 수 없습니다."),
    "analysis_target": ("분석할 측정 구분·공정·PARA를 확인해 주세요.", "등록된 이름과 일치하는 분석 대상이 필요합니다. 비슷한 이름으로 임의 대응하지 않습니다."),
    "analysis_scope": ("분석할 데이터 범위와 추가 조건은 무엇인가요?", "데이터셋 필터·Lot 범위 등 현재 분석 조건으로 표현되지 않는 범위를 확인해야 합니다."),
    "analysis_method": ("원인·이상치·상관 분석의 방법과 판단 기준은 무엇인가요?", "현재 도구는 기술 통계와 평균 비교를 제공합니다. 원인·이상치·통계적 유의성을 확정할 기준은 별도 확인이 필요합니다."),
}


def condition_description(request):
    parts = [f"분석 종류: {dict(statistics='기본 통계', trend='기간 비교', split='recipe 그룹 비교')[request.kind]}"]
    for label, value in (("측정 구분", request.family), ("공정", request.operation), ("PARA", request.parameter),
                         ("그룹 A recipe", request.recipe_a), ("그룹 B recipe", request.recipe_b),
                         ("시작", request.start), ("종료", request.end), ("기준 시작", request.baseline_start),
                         ("기준 종료", request.baseline_end), ("비교 시작", request.current_start), ("비교 종료", request.current_end),
                         ("A lot", ", ".join(request.lot_ids_a or [])), ("B lot", ", ".join(request.lot_ids_b or []))):
        if value is not None and value != "":
            parts.append(f"{label}: {value}")
    return " / ".join(parts)


def ensure_questions(db, request, snapshot, actor_id, missing, *, context_note=""):
    if not missing:
        return []
    datasets = inventory(db)
    ids = []
    scope = request.model_dump(mode="json", exclude={"knowledge_refs"})
    if scope.get("project_id") is None:
        scope.pop("project_id", None)
    # Each question is attached to an existing visible dataset, preserving its project tag.
    source_ids = {s["source_id"] for s in snapshot.values()}
    candidates = [d for d in datasets.values() if d["spec"].get("source_id") in source_ids]
    candidates.sort(key=lambda d: (bool(d["spec"].get("raw_sql")), d["project_id"] is not None, d["name"]))
    if request.project_id is not None:
        candidates = [d for d in candidates if d["project_id"] in (None, request.project_id)]
        candidates.sort(key=lambda d: d["project_id"] != request.project_id)
    dataset = candidates[0] if candidates else None
    if dataset is None:
        raise HTTPException(422, "분석 기준을 질문함에서 확인하려면 해당 원천의 데이터셋이 필요합니다. 데이터 관리를 확인하세요.")
    for condition in missing:
        if condition in {"split_groups", "recipe_crossover"} and "history" in snapshot:
            history_id = snapshot["history"]["source_id"]
            dataset = next((d for d in candidates if d["spec"].get("source_id") == history_id), candidates[0])
        else:
            dataset = next((d for d in candidates if d["spec"].get("source_id") != snapshot.get("history", {}).get("source_id")), candidates[0])
        fingerprint = digest(["analysis-condition-v1", database.source_database_key(), digest(dataset), scope, condition] + ([context_note] if context_note else []))
        question = db.scalar(select(KnowledgeQuestion).where(KnowledgeQuestion.fingerprint == fingerprint))
        if question is None:
            title, reason = QUESTION_TEXT[condition]
            context = " · ".join(str(v) for v in (request.family, request.operation, request.parameter) if v)
            conditions = condition_description(request)
            question = KnowledgeQuestion(connection_key=database.source_database_key(), fingerprint=fingerprint,
                title=title, reason=(f"분석 대상: {context}. " if context else "전체 PARA 분석. ") + reason + "\n요청 조건: " + conditions[:4000] + ("\n사용자 요청: " + context_note[:2000] if context_note else ""),
                kind="term", topic="other", subject="analysis:" + condition, targets=[], dataset=dataset,
                context_source_ids=sorted(source_ids),
                status="open", created_by=actor_id)
            db.add(question)
            db.flush()
        ids.append(question.id)
    return ids


def question_context(db, runs):
    """Load shared visibility/knowledge and question records once per response."""
    identities = {identity for run in runs for identity in run.question_ids}
    if not identities:
        return {}, {}, {}, {}
    datasets = inventory(db)
    approved = {(item["id"], item["version"]): item for item in approved_knowledge(db)}
    questions = {question.id: question for question in db.scalars(
        select(KnowledgeQuestion).where(KnowledgeQuestion.id.in_(identities)))}
    drafts = {}
    for draft in db.scalars(select(CollectedDraft).where(CollectedDraft.question_id.in_(identities),
        CollectedDraft.status == "approved").order_by(CollectedDraft.reviewed_at.desc())):
        drafts.setdefault(draft.question_id, draft)
    return datasets, approved, questions, drafts


def questions_json(db, run, *, context=None):
    if not run.question_ids:
        return []
    datasets, approved, questions, drafts = context if context is not None else question_context(db, [run])
    result = []
    for identity in run.question_ids:
        question = questions.get(identity)
        if not question or not question_visible(question, datasets, db=db):
            continue
        value = {k: getattr(question, k) for k in ("id", "title", "status")}
        draft = drafts.get(identity)
        current = approved.get((draft.item_id, draft.item_version)) if draft else None
        if current:
            value["approved_definition"] = {"item_id": draft.item_id, "version": draft.item_version,
                                            "content": current["content"]}
        result.append(value)
    return result


def run_json(db, run, *, details=True, questions_context=None, user=None):
    result = {k: getattr(run, k) for k in ("id", "kind", "status", "request", "error", "created_at", "started_at", "completed_at", "source_watermark")}
    result["questions"] = questions_json(db, run, context=questions_context)
    result["result"] = run.result if details else None
    result["method_version"] = (run.result or {}).get("method_version", METHOD_VERSION) if details else METHOD_VERSION
    result.update(revision=run.revision, retry_of=run.retry_of, retry_run_id=run.retry_run_id,
                  schedule_id=run.schedule_id, scheduled_for=run.scheduled_for,
                  cancel_requested_at=run.cancel_requested_at, cancelled_at=run.cancelled_at,
                  controls=analysis_controls(run, user).model_dump(mode='json'))
    return result


def find_run(db, identity):
    run = db.get(AnalysisRun, identity)
    if not run or run.connection_key != database.source_database_key():
        raise HTTPException(404, "현재 DB의 분석 결과를 찾을 수 없습니다.")
    validate_snapshot(db, run.source_snapshot, allow_column_additions=run.status == 'completed')
    scope = run.request.get("_project_scope")
    if scope and scope != project_scope(db, run.request["project_id"]):
        raise HTTPException(409, "프로젝트 범위가 변경되었습니다. 현재 범위에서 새 분석을 요청하세요.")
    if run.kind == "evaluation":
        from ..evaluation_analysis import check_scope
        check_scope(db, run)
    return run


def project_scope(db, project_id):
    if project_id is None:
        return None
    from ..project_settings import managed_project
    project = managed_project(project_id, db=db)
    if not project:
        raise HTTPException(422, "이 프로젝트의 lot_cd 범위가 정의되어 있지 않습니다. 프로젝트 범위를 먼저 확인하세요.")
    return {"id": project.id, "name": project.name, "lot_cds": sorted(project.lot_cds)}


def serialize_creation(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(7126002032)"))


def create_run(db, request, user, *, blockers=(), extra_questions=(), context_note=""):
    serialize_creation(db)
    live = db.get(User, user.id)
    if not live or not live.active or live.role not in {"admin", "editor"}:
        raise HTTPException(403, "분석 실행에는 편집 권한이 필요합니다.")
    scope = project_scope(db, request.project_id)
    snapshot, issues = source_snapshot(db)
    snapshot = selected_snapshot(snapshot, request)
    known = {(k["id"], k["version"]) for k in approved_knowledge(db)}
    if any((ref.item_id, ref.version) not in known for ref in request.knowledge_refs):
        raise HTTPException(422, "현재 DB에서 유효한 승인 지식만 분석 근거로 연결하세요.")
    pending = db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running", "cancelling"])))
    if pending >= 5:
        raise HTTPException(429, "대기 중인 분석이 5개입니다. 완료 후 실행하세요.")
    recent = db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.created_by == user.id,
                        AnalysisRun.created_at > utcnow() - timedelta(hours=1)))
    if recent >= 30:
        raise HTTPException(429, "분석 요청은 사용자별 시간당 30회까지 가능합니다.")
    missing = list(dict.fromkeys(request.missing() + list(blockers)))
    questions = ([] if request.purpose == "validation" else
                 ensure_questions(db, request, snapshot, user.id, request.missing() + request.missing_criteria()))
    if extra_questions or blockers:
        questions = list(dict.fromkeys(questions + ensure_questions(db, request, snapshot, user.id,
            [key for key in dict.fromkeys([*blockers, *extra_questions]) if key not in request.missing()], context_note=context_note)))
    stored_request = request.model_dump(mode="json")
    if scope:
        stored_request["_project_scope"] = scope
    run = AnalysisRun(connection_key=database.source_database_key(), kind=request.kind,
                      status="needs_input" if missing else "queued", request=stored_request,
                      source_snapshot=snapshot, question_ids=questions, created_by=user.id)
    db.add(run)
    db.flush()
    db.add(Audit(user_id=user.id, action="analytics.create", entity_id=run.id,
                 details={"kind": run.kind, "missing": missing, "criteria_missing": request.missing_criteria()}))
    return run


def compute_request(request, measurements, histories):
    from .compute import compute_split, compute_statistics, compute_trend
    if request.kind == "statistics":
        return compute_statistics(measurements)
    criteria = {"retest_policy": request.retest_policy, "min_samples": request.min_samples, "absolute_delta": request.absolute_delta}
    if request.kind == "trend":
        result = compute_trend(measurements, baseline_start=request.baseline_start, baseline_end=request.baseline_end,
                               current_start=request.current_start, current_end=request.current_end, bucket=request.bucket, **criteria)
    else:
        result = compute_split(measurements, histories, operation=request.operation, recipe_a=request.recipe_a,
                         recipe_b=request.recipe_b, start=request.start, end=request.end,
                         lot_ids_a=request.lot_ids_a, lot_ids_b=request.lot_ids_b, **criteria)
    overrides = {(c.family, c.operation, c.parameter): c for c in request.series_criteria}
    used = set()
    for series in result["series"]:
        key = (series["family"], series["operation"], series["parameter"])
        rule = overrides.get(key)
        series["criteria"] = {"min_samples": rule.min_samples if rule else request.min_samples,
                              "absolute_delta": rule.absolute_delta if rule else request.absolute_delta,
                              "sample_count_basis": "valid_measurements"}
        if rule:
            used.add(key)
            a, b = (series["baseline"], series["current"]) if request.kind == "trend" else (series["group_a"], series["group_b"])
            if min(a["valid_count"], b["valid_count"]) < rule.min_samples or series["delta"] is None:
                series["status"] = "insufficient_data"
            else:
                series["status"] = "changed" if series["delta"] != 0 and abs(series["delta"]) >= rule.absolute_delta else "stable"
    result["unused_criteria"] = [c.model_dump() for key, c in overrides.items() if key not in used]
    return result


def process_run(identity):
    """Called only after an atomic worker claim, inside the captured source binding."""
    try:
        with database.SessionLocal() as dispatch_db:
            kind = dispatch_db.scalar(select(AnalysisRun.kind).where(AnalysisRun.id == identity))
        if kind == "evaluation":
            from ..evaluation_analysis import process_run as process_evaluation
            process_evaluation(identity)
            return
        with database.SessionLocal.begin() as db:
            db.scalar(select(AnalysisRun).where(AnalysisRun.id == identity).with_for_update())
            run = find_run(db, identity)
            if run.status == 'cancelling':
                finish_cancel(run)
                return
            if run.status != "running":
                return
            actor = db.get(User, run.created_by)
            if not actor or not actor.active or actor.role not in {"admin", "editor"}:
                raise HTTPException(403, "요청자의 분석 실행 권한이 변경되었습니다.")
            request = AnalysisRequest.model_validate({k: v for k, v in run.request.items() if not k.startswith("_")})
            if run.schedule_id:
                from .schedules import check_context
                check_context(db, run.request, run.source_snapshot, METHOD_VERSION)
            scope = run.request.get("_project_scope")
            snapshot = run.source_snapshot
        checked_at = 0.0
        def probe():
            nonlocal checked_at
            now = monotonic()
            if now - checked_at < 0.25:
                return
            with database.SessionLocal() as status_db:
                status = status_db.scalar(select(AnalysisRun.status).where(AnalysisRun.id == identity))
            checked_at = now
            if status != 'running':
                raise AnalysisCancelled()
        with checking(probe):
            measurements, histories, watermark = (extract(snapshot, request, project_lot_cds=scope["lot_cds"])
                                                   if scope else extract(snapshot, request))
            result = compute_request(request, measurements, histories)
        result["method_version"] = METHOD_VERSION
        result["purpose"] = request.purpose
        result["knowledge_refs"] = [r.model_dump() for r in request.knowledge_refs]
        result["interpretation"] = "기술 통계 및 사용자가 지정한 변화량 기준입니다. 통계적 유의성·규격 이탈·인과효과 판정이 아닙니다."
        result["warnings"] = ["유효 표본 수는 측정 건수입니다. 독립 실험 수가 아니며 통계적 유의성이나 인과효과를 뜻하지 않습니다."]
        if result.get("unused_criteria"):
            result["warnings"].append("판정 기준을 지정했지만 해당 기간에 측정값이 없는 PARA가 있습니다.")
        if request.kind == "split":
            result["warnings"].append("각 측정 시점 이전의 공정 이력으로 recipe를 대응한 관찰 비교입니다.")
            if result.get("overlapping_lots"):
                result["warnings"].append("같은 lot의 서로 다른 wafer가 두 그룹에 포함되어 있습니다. 두 그룹을 독립 실험으로 해석하지 마세요.")
            if result.get("excluded_ambiguous_wafer_count"):
                result["warnings"].append("Recipe 대응이 모호한 wafer가 있습니다. 해당 wafer를 제외한 임시 집계이며 공정 재방문 처리 기준 확인이 필요합니다.")
        if request.purpose == "validation":
            result["warnings"].insert(0, "구현 검증용 실행입니다. 입력 기간·재측정 조건을 업무 기준으로 승인하거나 저장하지 않았습니다.")
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > 20_000_000:
            raise HTTPException(422, "분석 결과가 저장 한도를 초과했습니다. 기간 또는 PARA 범위를 줄여 주세요.")
        with database.SessionLocal.begin() as db:
            db.scalar(select(AnalysisRun).where(AnalysisRun.id == identity).with_for_update())
            run = find_run(db, identity)
            if run.status == 'cancelling':
                finish_cancel(run)
                return
            if run.status != "running":
                return
            if run.schedule_id:
                from .schedules import check_context
                check_context(db, run.request, run.source_snapshot, METHOD_VERSION)
            run.result, run.source_watermark = result, watermark
            run.status = "completed"
            if request.purpose != "validation" and any(s.get("status") == "needs_criteria" for s in result["series"]):
                run.question_ids = list(dict.fromkeys(run.question_ids + ensure_questions(
                    db, request, snapshot, run.created_by, ["change_criteria"])))
            if result.get("excluded_ambiguous_wafer_count", 0):
                run.status = "needs_input"
                if request.purpose != "validation":
                    run.question_ids = list(dict.fromkeys(run.question_ids + ensure_questions(
                        db, request, snapshot, run.created_by, ["recipe_crossover"])))
            run.completed_at, run.lease_until = utcnow(), None
            run.revision += 1
    except Exception as error:
        # Provider/DB exceptions may contain connection URLs and SQL values.
        message = error.detail if isinstance(error, HTTPException) else "분석 계산에 실패했습니다. 입력 범위와 데이터 형식을 확인하세요."
        with database.SessionLocal.begin() as db:
            run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == identity).with_for_update())
            if run and run.status == 'cancelling':
                finish_cancel(run)
            elif run and run.status == "running":
                run.status, run.error, run.completed_at, run.lease_until = "failed", str(message), utcnow(), None
                run.revision += 1


def finish_cancel(run):
    run.status, run.cancelled_at, run.completed_at, run.lease_until = 'cancelled', utcnow(), utcnow(), None
    run.revision += 1
