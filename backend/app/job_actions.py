"""Revision-guarded actions. Retry creates a linked run; original evidence survives."""
from fastapi import HTTPException
from sqlalchemy import select

from . import db as database
from .activity_contract import ActivityActionResult
from .analytics.models import AnalysisRun
from .analytics.proposals import (check_proposal_scope, live_editor, lock_active_workspace, owned_proposal)
from .analytics.schemas import AnalysisRequest
from .analytics.service import create_run, find_run, serialize_creation
from .analytics.worker import wake_worker
from .models import Audit, utcnow
from .semantic.collector_service import future
from .semantic.collector_service import claim_generation, ensure_settings, finish_collection_cancel, generate_questions
from .semantic import collector_config


def revision(current, expected):
    if current != expected:
        raise HTTPException(409, '작업 상태가 변경되었습니다. 최신 상태를 확인한 뒤 다시 선택하세요.')


def response(target, status, message):
    return ActivityActionResult(target=target, status=status, message=message)


def analysis_action(body, user):
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        actor = live_editor(db, user.id)
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == body.target.id).with_for_update())
        run = find_run(db, body.target.id)  # source and project visibility, also for repeated actions
        if actor.role != 'admin' and run.created_by != actor.id:
            raise HTTPException(403, '본인이 요청한 분석만 재시도·취소할 수 있습니다.')
        if body.target.project_id != run.request.get('project_id'):
            raise HTTPException(409, '분석의 프로젝트 범위를 다시 확인하세요.')
        if body.action == 'cancel':
            if run.status not in {'cancelling', 'cancelled'}:
                revision(run.revision, body.expected_revision)
                if run.status not in {'queued', 'running'}:
                    raise HTTPException(409, '이미 끝났거나 기준 확인 중인 분석은 실행 취소 대상이 아닙니다.')
                run.cancel_requested_at = utcnow()
                if run.status == 'queued' or not future(run.lease_until):
                    run.status, run.cancelled_at, run.completed_at, run.lease_until = 'cancelled', utcnow(), utcnow(), None
                else:
                    run.status = 'cancelling'
                run.revision += 1
                db.add(Audit(user_id=actor.id, action='analytics.cancel', entity_id=run.id, details={'status': run.status}))
            result = response(body.target, run.status, '취소했습니다. 기존 질문·답변·승인 내용은 유지됩니다.' if run.status == 'cancelled'
                else '취소를 요청했습니다. 실행이 중단되는지 확인 중이며 새 결과는 반영하지 않습니다.')
        else:
            if run.retry_run_id:
                if body.expected_revision not in {run.revision, run.revision - 1}:
                    revision(run.revision, body.expected_revision)
                child = find_run(db, run.retry_run_id)
            else:
                revision(run.revision, body.expected_revision)
                if run.status not in {'failed', 'cancelled'}:
                    raise HTTPException(409, '실패하거나 취소가 완료된 분석만 같은 조건으로 재시도할 수 있습니다.')
                if run.kind == 'evaluation':
                    from .evaluation_analysis import retry_run
                    child = retry_run(db, run, actor)
                else:
                    request = AnalysisRequest.model_validate({k: v for k, v in run.request.items() if not k.startswith('_')})
                    child = create_run(db, request, actor)
                if child.source_snapshot != run.source_snapshot or child.request.get('_project_scope') != run.request.get('_project_scope'):
                    raise HTTPException(409, '분석 원천이나 프로젝트 범위가 변경되었습니다. 조건을 확인해 새 분석을 요청하세요.')
                child.retry_of, run.retry_run_id = run.id, child.id
                if run.schedule_id:
                    from .analytics.models import AnalysisSchedule
                    schedule = db.get(AnalysisSchedule, run.schedule_id)
                    child.schedule_id, child.scheduled_for = run.schedule_id, run.scheduled_for
                    child.comparison_run_id = run.comparison_run_id
                    # An old retry remains linked history; it cannot replace the newest slot.
                    if schedule and schedule.last_run_id == run.id:
                        schedule.last_run_id = child.id
                        schedule.revision += 1
                run.revision += 1
                db.add(Audit(user_id=actor.id, action='analytics.retry', entity_id=run.id, details={'run_id': child.id}))
            result = response({'kind': 'analysis_run', 'id': child.id, 'project_id': body.target.project_id}, child.status,
                '같은 조건의 재시도 작업을 열었습니다. 원래 기록은 보존되며 원천 데이터는 다시 조회합니다.')
    if result.status == 'queued':
        wake_worker()
    return result


def proposal_action(body, user):
    if body.action != 'cancel':
        raise HTTPException(422, '실행 전 조건안은 취소하거나 조건을 검토할 수 있습니다.')
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        proposal = owned_proposal(db, body.target.id, user.id, lock=True)
        check_proposal_scope(db, proposal, user.id, body.target.project_id)
        if proposal.status != 'cancelled':
            revision(proposal.revision, body.expected_revision)
            if proposal.status != 'pending':
                raise HTTPException(409, '이미 실행한 조건안입니다. 연결된 분석의 상태를 확인하세요.')
            proposal.status, proposal.revision = 'cancelled', proposal.revision + 1
            db.add(Audit(user_id=user.id, action='analytics.proposal.cancel', entity_id=proposal.id, details={}))
        return response(body.target, 'cancelled', '조건안을 취소했습니다. 분석이나 질문을 새로 만들지 않았습니다.')


def generate_bound(claim, binding):
    with database.use_source(binding):
        generate_questions(claim=claim)


def collection_action(body, user, tasks):
    if body.target.id is not None or body.target.project_id is not None:
        raise HTTPException(422, '지식 수집은 현재 DB 전체 범위의 작업입니다.')
    with database.SessionLocal.begin() as db:
        lock_active_workspace(db)
        actor = live_editor(db, user.id)
        if actor.role != 'admin':
            raise HTTPException(403, '관리자만 지식 수집을 재시도·취소할 수 있습니다.')
        cfg = ensure_settings(db)
        if body.action == 'cancel':
            if cfg.last_status not in {'cancelling', 'cancelled'}:
                revision(cfg.execution_revision, body.expected_revision)
                if cfg.last_status != 'running':
                    raise HTTPException(409, '현재 실행 중인 수집 작업이 없습니다.')
                if future(cfg.lease_until):
                    cfg.last_status, cfg.last_message = 'cancelling', '취소 요청을 확인 중입니다. 새 질문은 반영하지 않습니다.'
                    cfg.execution_revision += 1
                else:
                    finish_collection_cancel(cfg)
                db.add(Audit(user_id=actor.id, action='semantic.collection.cancel', entity_id=cfg.connection_key, details={}))
            return response(body.target, cfg.last_status, cfg.last_message)
        revision(cfg.execution_revision, body.expected_revision)
        if cfg.last_status not in {'error', 'cancelled'}:
            raise HTTPException(409, '실패하거나 취소가 완료된 수집만 재시도할 수 있습니다.')
        if not collector_config.configured():
            raise HTTPException(422, '지식 수집 연결 설정을 확인하세요.')
        claim = claim_generation(db, actor_id=actor.id)
        db.add(Audit(user_id=actor.id, action='semantic.collection.retry_generation', entity_id=cfg.connection_key, details={}))
    tasks.add_task(generate_bound, claim, database.current_source())
    return response(body.target, 'running', '현재 설정으로 수집을 다시 시작했습니다. 기존 질문은 중복 생성하지 않습니다.')
