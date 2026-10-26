"""Private, durable analysis conditions. Only explicit confirmation creates a run."""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, text, update

from .. import db as database
from ..models import Audit, DatabaseMount, User, utcnow
from .data import catalog, selected_snapshot, source_snapshot, validate_snapshot
from .models import AnalysisProposal
from .proposal_contract import AnalysisProposalConfirm, AnalysisProposalView
from .schemas import AnalysisRequest
from .service import create_run, find_run, project_scope, run_json, serialize_creation
from .worker import wake_worker


# These are the fields editable in the review form. Identity and scope are fixed.
EDITABLE_FIELDS = frozenset({
    "family", "operation", "parameter", "start", "end", "baseline_start", "baseline_end",
    "current_start", "current_end", "recipe_a", "recipe_b", "lot_ids_a", "lot_ids_b",
    "retest_policy", "min_samples", "absolute_delta", "series_criteria", "knowledge_refs",
})
TARGET_FIELDS = frozenset({"family", "operation", "parameter", "recipe_a", "recipe_b"})


def live_editor(db, user_id):
    user = db.get(User, user_id)
    if not user or not user.active or user.role not in {"admin", "editor"}:
        raise HTTPException(403, "분석 조건 확인과 실행에는 편집 권한이 필요합니다.")
    return user


def owned_proposal(db, identity, user_id, *, lock=False):
    query = select(AnalysisProposal).where(AnalysisProposal.id == identity,
        AnalysisProposal.owner_id == user_id, AnalysisProposal.connection_key == database.source_database_key())
    proposal = db.scalar(query.with_for_update() if lock else query)
    if proposal is None:
        raise HTTPException(404, "현재 DB에서 이 분석 조건안을 찾을 수 없습니다.")
    return proposal


def check_proposal_scope(db, proposal, user_id, project_id):
    actor = live_editor(db, user_id)
    if actor.role != proposal.owner_role:
        raise HTTPException(409, "사용자 권한이 변경되었습니다. 현재 권한으로 분석을 다시 요청하세요.")
    if proposal.project_id != project_id:
        raise HTTPException(409, "이 조건안의 프로젝트로 이동한 뒤 확인하세요.")
    if proposal.workspace_token != database.workspace_token():
        raise HTTPException(409, "DB 설정이 변경되었습니다. 현재 DB에서 분석을 다시 요청하세요.")
    if proposal.project_scope != project_scope(db, proposal.project_id):
        raise HTTPException(409, "프로젝트 범위가 변경되었습니다. 현재 범위로 분석을 다시 요청하세요.")
    validate_snapshot(db, proposal.source_snapshot)
    current, _ = source_snapshot(db)
    if current != proposal.source_snapshot:
        raise HTTPException(409, "분석 원천 또는 스키마가 변경되었습니다. 새 조건안을 확인하세요.")
    return actor


def lock_active_workspace(db):
    # Database activation/visibility changes use this lock as well. A request's
    # captured SourceBinding must not confirm after a different mount is active.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(7126002004)"))
    binding = database.current_source()
    if binding.profile_id:
        mount = db.get(DatabaseMount, 1)
        if not mount or mount.profile_id != binding.profile_id or mount.revision != binding.revision:
            raise HTTPException(409, "활성 DB가 변경되었습니다. 현재 DB에서 분석 조건을 다시 확인하세요.")


def target_supported(request, available):
    selected = {key: getattr(request, key) for key in ("family", "parameter") if getattr(request, key)}
    if request.kind != "split" and request.operation:
        selected["operation"] = request.operation
    if selected and not any(all(series.get(key) == value for key, value in selected.items())
                            for series in available["series"]):
        return False
    if request.kind == "split" and request.operation:
        operations = [entry for entry in available["recipe_operations"] if entry["operation"] == request.operation]
        if not operations or any(recipe and recipe not in operations[0]["recipes"] for recipe in (request.recipe_a, request.recipe_b)):
            return False
    known_series = {(entry["family"], entry.get("operation") or "", entry["parameter"]) for entry in available["series"]}
    return all((entry.family, entry.operation, entry.parameter) in known_series for entry in request.series_criteria)


def proposal_json(proposal):
    request = AnalysisRequest.model_validate(proposal.confirmed_request or proposal.request)
    context = proposal.context
    blockers = context.get("confirmed_blockers", context.get("blockers", []))
    missing = list(dict.fromkeys(request.missing() + blockers))
    warnings = []
    if request.missing_criteria():
        warnings.append("판정 기준을 비워 두면 수치 차이를 계산하고 변화 여부는 판정하지 않습니다.")
    if any(value in blockers for value in ("analysis_scope", "analysis_method")):
        warnings.append("지원하지 않는 범위나 분석 방법은 확인만으로 해제되지 않습니다. 질문함에서 기준을 확인해야 합니다.")
    if context.get("goal") in {"cause", "outlier"}:
        warnings.append("이 분석은 원인이나 이상치를 확정하지 않습니다.")
    explanation = ("확인한 조건으로 분석을 요청했습니다. 분석 결과에서 진행 상태를 확인하세요."
        if proposal.status == "confirmed" else
        "취소한 조건안입니다. 분석을 실행하지 않았습니다. 필요한 경우 새 조건안을 요청하세요." if proposal.status == 'cancelled' else
        "분석 조건을 확인하고 필요한 값을 수정하세요. 아직 계산하거나 질문함에 등록하지 않았습니다.")
    evidence = context.get("evidence", {})
    if proposal.confirmed_request:
        evidence = {key: quote for key, quote in evidence.items()
            if proposal.confirmed_request.get(key) == proposal.request.get(key)}
    display = context.get("display", {}) if request.kind == "split" else {"family": request.family, "parameter": request.parameter}
    targets = [(proposal.project_scope or {}).get('name') or '현재 DB 전체']
    if request.operation:
        targets.append(f'공정 {request.operation}')
    if display.get('parameter'):
        targets.append(display['parameter'])
    explanation = '해석한 범위: ' + ' · '.join(targets) + '. ' + explanation
    return AnalysisProposalView(proposal_id=proposal.id, revision=proposal.revision, status=proposal.status,
        workspace_token=proposal.workspace_token, project_id=proposal.project_id, project_scope=proposal.project_scope,
        request=request, goal=context.get("goal", "describe"), display=display,
        missing=missing, criteria_missing=request.missing_criteria(), blockers=blockers, warnings=warnings,
        evidence=evidence, explanation=explanation, created_at=proposal.created_at,
        run_id=proposal.run_id).model_dump(mode="json")


def create_proposal(call, chat_request, user_id, available, conversation_id=None, turn_sequence=None, *, origin_project_id=...):
    from .chat_tools import CONDITION_FIELDS, prepare_request
    request, blockers, extra = prepare_request(call, chat_request, available)
    with database.SessionLocal.begin() as db:
        actor = live_editor(db, user_id)
        serialize_creation(db)
        lock_active_workspace(db)
        recent = db.scalar(select(func.count()).select_from(AnalysisProposal).where(
            AnalysisProposal.owner_id == user_id, AnalysisProposal.created_at > utcnow() - timedelta(hours=1)))
        if recent >= 30:
            raise HTTPException(429, "분석 조건안은 사용자별 시간당 30회까지 만들 수 있습니다.")
        snapshot, _ = source_snapshot(db)
        selected_snapshot(snapshot, request)
        scope = project_scope(db, request.project_id)
        proposal = AnalysisProposal(owner_id=user_id, owner_role=actor.role,
            connection_key=database.source_database_key(), workspace_token=database.workspace_token(),
            project_id=request.project_id, project_scope=scope, conversation_id=conversation_id, turn_sequence=turn_sequence,
            request=request.model_dump(mode="json"), source_snapshot=snapshot,
            context={"goal": call.goal, "display": {"family": call.family, "parameter": call.parameter},
                "origin_project_id": chat_request.project_id if origin_project_id is ... else origin_project_id,
                "blockers": blockers, "extra_questions": extra, "context_note": chat_request.message,
                "evidence": {key: call.evidence[key] for key in CONDITION_FIELDS
                    if getattr(request, key, None) is not None and key in call.evidence}})
        db.add(proposal)
        db.flush()
        db.add(Audit(user_id=user_id, action="analytics.propose", entity_id=proposal.id,
            details={"kind": request.kind, "missing": request.missing(), "blockers": blockers}))
        return proposal_json(proposal)


def get_proposal(identity, user_id, project_id):
    with database.SessionLocal() as db:
        proposal = owned_proposal(db, identity, user_id)
        check_proposal_scope(db, proposal, user_id, project_id)
        if proposal.run_id:
            find_run(db, proposal.run_id)
        return proposal_json(proposal)


def restore_proposal(response, user_id, project_id):
    identity = response.get("proposal_id", "")
    try:
        with database.SessionLocal() as db:
            proposal = owned_proposal(db, identity, user_id)
            if proposal.context.get('origin_project_id', proposal.project_id) != project_id:
                raise HTTPException(409, '이 조건안의 원래 대화 범위가 다릅니다.')
            target_project = proposal.project_id
        return get_proposal(identity, user_id, target_project)
    except HTTPException:
        return AnalysisProposalView(proposal_id=identity, status="unavailable",
            explanation="사용자 권한·DB·프로젝트 또는 분석 원천이 변경되어 이전 조건안을 사용할 수 없습니다. 현재 화면에서 다시 요청하세요.").model_dump(mode="json")


def check_confirmation(proposal, body, values):
    if body.workspace_token != proposal.workspace_token:
        raise HTTPException(409, "현재 DB의 조건안을 다시 불러온 뒤 확인하세요.")
    if any(values.get(key) != proposal.request.get(key) for key in set(values) - EDITABLE_FIELDS):
        raise HTTPException(422, "분석 유형·목적·프로젝트는 조건안에서 변경할 수 없습니다. 새 분석을 요청하세요.")
    if proposal.status == "confirmed":
        if values != proposal.confirmed_request or body.expected_revision not in {proposal.revision, proposal.revision - 1}:
            raise HTTPException(409, "이미 확인한 조건안입니다. 결과를 열거나 새 분석을 요청하세요.")
    elif proposal.status != "pending" or body.expected_revision != proposal.revision:
        raise HTTPException(409, "조건안이 갱신되었습니다. 최신 조건안을 불러와 확인하세요.")


def confirm_proposal(identity, body: AnalysisProposalConfirm, user_id):
    values = body.request.model_dump(mode="json")
    # DISTINCT/extent reads can be slow. Never hold the shared creation or
    # workspace locks while reading the source catalog. Recheck all authority
    # and snapshot data in the write transaction after this read completes.
    with database.SessionLocal() as db:
        proposal = owned_proposal(db, identity, user_id)
        check_proposal_scope(db, proposal, user_id, body.project_id)
        check_confirmation(proposal, body, values)
        needs_catalog = proposal.status == "pending"
    available = None
    if needs_catalog:
        with database.SessionLocal() as db:
            available = catalog(db)
    result = None
    created = False
    with database.SessionLocal.begin() as db:
        # Shared with create_run/worker: serialize before taking a proposal lock.
        serialize_creation(db)
        lock_active_workspace(db)
        proposal = owned_proposal(db, identity, user_id, lock=True)
        actor = check_proposal_scope(db, proposal, user_id, body.project_id)
        check_confirmation(proposal, body, values)
        if proposal.status == "confirmed":
            result = run_json(db, find_run(db, proposal.run_id), user=actor)
        else:
            if available is None:
                raise HTTPException(409, "조건안이 갱신되었습니다. 최신 조건안을 불러와 확인하세요.")
            if not available["supported"]:
                raise HTTPException(422, "현재 DB에서 분석 가능한 원천을 확인하세요.")
            selected_snapshot(proposal.source_snapshot, body.request)
            blockers = list(proposal.context.get("blockers", []))
            if "period" not in body.request.missing():
                blockers = [value for value in blockers if value != "period"]
            valid_target = target_supported(body.request, available)
            target_changed = any(values[key] != proposal.request[key] for key in TARGET_FIELDS)
            if valid_target and target_changed:
                blockers = [value for value in blockers if value != "analysis_target"]
            if not valid_target and "analysis_target" not in blockers:
                blockers.append("analysis_target")
            # Claim and run creation share one transaction. Rollback leaves the
            # proposal pending if schema, permission, quota or run creation fails.
            claimed = db.execute(update(AnalysisProposal).where(AnalysisProposal.id == proposal.id,
                AnalysisProposal.status == "pending", AnalysisProposal.revision == body.expected_revision)
                .values(status="confirming")).rowcount
            if claimed != 1:
                raise HTTPException(409, "다른 요청에서 이 조건안을 확인했습니다. 최신 상태를 불러오세요.")
            run = create_run(db, body.request, actor, blockers=blockers,
                extra_questions=proposal.context.get("extra_questions", []),
                context_note=proposal.context.get("context_note", ""))
            if (run.request.get("_project_scope") != proposal.project_scope or
                    run.source_snapshot != selected_snapshot(proposal.source_snapshot, body.request)):
                raise HTTPException(409, "확인 중 분석 범위가 변경되었습니다. 최신 조건안을 다시 확인하세요.")
            proposal.status, proposal.run_id = "confirmed", run.id
            proposal.confirmed_request, proposal.confirmed_at = values, utcnow()
            proposal.context = {**proposal.context, "confirmed_blockers": blockers}
            proposal.revision += 1
            db.add(Audit(user_id=user_id, action="analytics.confirm", entity_id=proposal.id,
                details={"run_id": run.id, "changed_fields": sorted(key for key in EDITABLE_FIELDS
                    if values[key] != proposal.request[key])}))
            result = run_json(db, run, user=actor)
            created = True
    if created and result["status"] == "queued":
        wake_worker()
    return result
