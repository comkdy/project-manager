"""Bounded activity summary; only the legacy project directory may read a source."""
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only

from . import db as database
from .activity_contract import ActivityActionRequest, ActivityActionResult, ActivityControls, ActivityFreshness, WorkspaceActivity, WorkspaceActivityItem
from .job_controls import analysis_controls, collection_controls
from .analytics.data import source_snapshot, validate_snapshot
from .analytics.models import AnalysisProposal, AnalysisRun
from .analytics.service import project_scope, question_context, questions_json
from .auth import get_current_user
from .catalog import projects_list
from .models import AppProject, ProjectRemoval, Source, utcnow
from .semantic.collector_models import CollectionSettings


router = APIRouter(prefix='/api/workspace', tags=['workspace activity'])
STATUS_ORDER = {'running': 0, 'cancelling': 0, 'queued': 1, 'needs_confirmation': 2, 'needs_input': 3,
                'failed': 4, 'unknown': 5, 'completed': 6, 'cancelled': 7}
RUN_STATUSES = {'queued', 'running', 'cancelling', 'cancelled', 'needs_input', 'completed', 'failed'}


def utc(value):
    """Metadata DateTime columns are written by utcnow; SQLite drops their zone."""
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def stored_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def freshness(run):
    watermark = run.source_watermark or {}
    if not isinstance(watermark, dict):
        return ActivityFreshness()
    queried = stored_time(watermark.get('captured_at'))
    measured = []
    watermarks = watermark.get('sources') or {}
    for family, entry in (watermarks.items() if isinstance(watermarks, dict) else []):
        if family == 'history' or not isinstance(entry, dict):
            continue
        source = run.source_snapshot.get(family)
        if source and entry.get('source_id') == source['source_id']:
            value = stored_time(entry.get('last_measured_at'))
            if value is not None:
                measured.append(value)
    latest = max(measured) if measured else None
    return ActivityFreshness(state='recorded' if queried or latest else 'unknown',
                             queried_at=queried, last_measured_at=latest)


def scope_visible(project_id, snapshot, projects, scopes, selected_project, *, proposal=False):
    if selected_project is not None and project_id != selected_project:
        return False
    if project_id is None:
        return snapshot is None
    if project_id not in projects:
        return False
    # Older source-project runs have no managed lot scope. New proposals always
    # require the same managed scope as their confirmation endpoint.
    return (project_id in scopes and snapshot == scopes[project_id]) or (
        not proposal and snapshot is None and project_id not in scopes)


def project_directory(db):
    try:
        names = {p['id']: p['name'] for p in projects_list()}
    except SQLAlchemyError:
        raise HTTPException(503, '프로젝트 목록을 확인하지 못해 작업 상태를 불러올 수 없습니다. DB 연결을 확인하세요.') from None
    managed = list(db.scalars(select(AppProject)))
    excluded = {p.id for p in managed if p.connection_key != database.source_database_key() or not p.active}
    excluded.update(db.scalars(select(ProjectRemoval.id)))
    names = {key: name for key, name in names.items() if key not in excluded}
    scopes = {p.id: project_scope(db, p.id) for p in managed if p.id in names}
    return names, scopes


def sources_visible(db, snapshot, sources, *, historical=False):
    if not snapshot:
        return False
    try:
        validate_snapshot(db, snapshot, sources=sources, allow_column_additions=historical)
        return True
    except HTTPException:
        return False


def title(request):
    if request.get('kind') == 'evaluation':
        return request.get('_evaluation_dataset', {}).get('name', '현재 데이터') + (' · 챗 요청 분석' if request.get('_chat_analysis') else ' · 사례 기반 분석')
    label = {'statistics': '기본 통계', 'trend': '기간 비교', 'split': 'Recipe 비교'}.get(request.get('kind'), '분석')
    target = ' · '.join(str(value) for value in (request.get('family', '').upper() if request.get('family') else '',
                       request.get('operation'), request.get('parameter')) if value)
    return (target + ' · ' if target else '') + label


def workspace_activity(user, project_id=None, limit=60):
    if not user.active:
        raise HTTPException(403, '사용이 중지된 계정입니다.')
    now = utcnow()
    with database.SessionLocal() as db:
        projects, scopes = project_directory(db)
        if project_id is not None and project_id not in projects:
            raise HTTPException(404, '현재 DB의 활성 프로젝트를 찾을 수 없습니다.')
        run_query = select(AnalysisRun).options(load_only(
            AnalysisRun.id, AnalysisRun.kind, AnalysisRun.status, AnalysisRun.request, AnalysisRun.source_snapshot,
            AnalysisRun.source_watermark, AnalysisRun.question_ids, AnalysisRun.created_at, AnalysisRun.started_at,
            AnalysisRun.completed_at, AnalysisRun.lease_until, AnalysisRun.created_by, AnalysisRun.revision,
            AnalysisRun.retry_run_id, raiseload=True)).where(
                AnalysisRun.connection_key == database.source_database_key())
        if project_id is not None:
            run_query = run_query.where(AnalysisRun.request['project_id'].as_integer() == project_id)
        runs = list(db.scalars(run_query.order_by(
            case(STATUS_ORDER, value=AnalysisRun.status, else_=STATUS_ORDER['unknown']),
            func.coalesce(AnalysisRun.completed_at, AnalysisRun.started_at, AnalysisRun.created_at).desc(),
            AnalysisRun.id).limit(101)))
        proposals = []
        if user.role in {'admin', 'editor'}:
            proposal_query = select(AnalysisProposal).options(load_only(
                AnalysisProposal.id, AnalysisProposal.project_id, AnalysisProposal.project_scope,
                AnalysisProposal.request, AnalysisProposal.source_snapshot, AnalysisProposal.created_at,
                AnalysisProposal.revision, AnalysisProposal.status,
                raiseload=True)).where(AnalysisProposal.connection_key == database.source_database_key(),
                AnalysisProposal.owner_id == user.id, AnalysisProposal.owner_role == user.role,
                AnalysisProposal.workspace_token == database.workspace_token(), AnalysisProposal.status.in_(['pending', 'cancelled']))
            if project_id is not None:
                proposal_query = proposal_query.where(AnalysisProposal.project_id == project_id)
            proposals = list(db.scalars(proposal_query.order_by(case((AnalysisProposal.status == 'pending', 0), else_=1),
                AnalysisProposal.created_at.desc(), AnalysisProposal.id).limit(101)))
        source_ids = {s['source_id'] for row in [*runs, *proposals] for s in row.source_snapshot.values()}
        sources = {s.id: s for s in db.scalars(select(Source).where(Source.id.in_(source_ids)))} if source_ids else {}
        runs = [run for run in runs if scope_visible(run.request.get('project_id'), run.request.get('_project_scope'), projects, scopes, project_id)
                and sources_visible(db, run.source_snapshot, sources, historical=run.status == 'completed')]
        current_snapshot = source_snapshot(db)[0] if proposals else {}
        proposals = [proposal for proposal in proposals if scope_visible(proposal.project_id, proposal.project_scope, projects, scopes, project_id, proposal=True)
                     and proposal.source_snapshot == current_snapshot and sources_visible(db, proposal.source_snapshot, sources)]
        shared_questions = question_context(db, runs)
        items = []
        for run in runs:
            if run.kind == 'evaluation':
                from .evaluation_analysis import checked_run
                try:
                    checked_run(db, run.id, user)
                except HTTPException:
                    continue
            scope = run.request.get('project_id')
            status = run.status if run.status in RUN_STATUSES else 'unknown'
            expired = status == 'running' and (run.lease_until is None or utc(run.lease_until) < now)
            if expired:
                status = 'unknown'
            items.append(WorkspaceActivityItem(id='analysis:' + run.id, kind='analysis_run', title=title(run.request),
                status=status, project_id=scope, project_name=projects.get(scope),
                created_at=utc(run.created_at), started_at=utc(run.started_at), completed_at=utc(run.completed_at),
                activity_at=utc(run.completed_at or run.started_at or run.created_at), freshness=freshness(run),
                question_count=len(questions_json(db, run, context=shared_questions)),
                message=('마지막 실행 기록의 유효 시간이 지나 진행 상태를 다시 확인해야 합니다.' if expired else
                         '취소 요청을 확인 중입니다. 새 결과는 반영하지 않습니다.' if status == 'cancelling' else
                         '취소가 완료되었습니다. 기존 질문·답변·승인은 유지됩니다.' if status == 'cancelled' else
                         '분석 결과에서 실패 사유를 확인하세요.' if status == 'failed' else ''),
                controls=analysis_controls(run, user),
                target={'kind': 'analysis_run', 'id': run.id, 'project_id': scope}))
        for proposal in proposals:
            scope = proposal.project_id
            items.append(WorkspaceActivityItem(id='proposal:' + proposal.id, kind='analysis_proposal',
                title=title(proposal.request), status='cancelled' if proposal.status == 'cancelled' else 'needs_confirmation', project_id=scope,
                project_name=projects.get(scope),
                created_at=utc(proposal.created_at), activity_at=utc(proposal.created_at),
                message='취소된 조건안입니다. 분석·질문을 만들지 않았습니다.' if proposal.status == 'cancelled' else '조건을 확인하기 전에는 분석을 실행하지 않습니다.',
                controls=ActivityControls(revision=proposal.revision, can_cancel=proposal.status == 'pending'),
                target={'kind': 'analysis_proposal', 'id': proposal.id, 'project_id': scope}))
        if user.role == 'admin' and project_id is None:
            cfg = db.get(CollectionSettings, database.source_database_key())
            if cfg is not None and cfg.last_status != 'idle':
                status = {'running': 'running', 'completed': 'completed', 'unchanged': 'completed',
                          'empty': 'completed', 'error': 'failed', 'cancelling': 'cancelling', 'cancelled': 'cancelled'}.get(cfg.last_status, 'unknown')
                if status == 'running' and (cfg.lease_until is None or utc(cfg.lease_until) < now):
                    status = 'unknown'
                items.append(WorkspaceActivityItem(id='collection:' + database.source_database_key(),
                    kind='knowledge_collection', title='업무 지식 질문 수집', status=status,
                    started_at=utc(cfg.last_run), activity_at=utc(cfg.last_run),
                    message={'completed': '저장된 수집 상태입니다. 원천 측정값의 최신성을 뜻하지 않습니다.',
                             'failed': '지식 수집 설정에서 실행 상태를 확인하세요.',
                             'cancelling': '취소 요청을 확인 중입니다. 새 질문은 반영하지 않습니다.',
                             'cancelled': '취소가 완료되었습니다. 자동 수집 일정과 기존 질문은 유지됩니다.',
                             'unknown': '저장된 기록만으로 현재 진행 상태를 확인할 수 없습니다.'}.get(status, ''),
                    controls=collection_controls(cfg, user),
                    target={'kind': 'knowledge_collection'}))
        items.sort(key=lambda item: (STATUS_ORDER[item.status],
                   -(item.activity_at.timestamp() if item.activity_at else 0), item.id))
        return WorkspaceActivity(workspace_token=database.workspace_token(), project_id=project_id,
            generated_at=now, limit=limit, has_more=len(items) > limit, items=items[:limit],
            notes=['최근 저장된 작업 기록을 표시합니다. 원천 측정값을 새로 조회하지 않습니다.',
                   '조회 시각은 해당 분석이 원천을 읽은 시각이며, 마지막 측정 시각은 그 분석 대상에 한정됩니다.',
                   '시각이 없는 작업의 데이터 최신성은 알 수 없습니다.'])


@router.get('/activity', response_model=WorkspaceActivity)
def activity(project_id: int | None = None, limit: int = Query(default=60, ge=1, le=100), user=Depends(get_current_user)):
    return workspace_activity(user, project_id, limit)


@router.post('/activity/actions', response_model=ActivityActionResult)
def activity_action(body: ActivityActionRequest, tasks: BackgroundTasks, user=Depends(get_current_user)):
    from .job_actions import analysis_action, collection_action, proposal_action
    if body.workspace_token != database.workspace_token():
        raise HTTPException(409, '작업공간이 변경되었습니다. 현재 작업 목록을 새로고침하세요.')
    if body.target.kind == 'knowledge_collection':
        return collection_action(body, user, tasks)
    if not body.target.id:
        raise HTTPException(422, '작업 식별자가 필요합니다.')
    return (analysis_action if body.target.kind == 'analysis_run' else proposal_action)(body, user)
