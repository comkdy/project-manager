"""Case-guided data checks reuse evaluation jobs and immutable knowledge review."""
from copy import deepcopy
from datetime import timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select

from . import db as database
from .analytics.models import AnalysisRun, KnowledgeWatch
from .analytics.proposals import live_editor, lock_active_workspace
from .analytics.service import serialize_creation
from .auth import require_editor
from .evaluation_analysis import Search, _compile, _quota, checked_run
from .evaluation_compute import data_fingerprint
from .models import Audit, User, utcnow
from .schemas import StrictModel
from .semantic.evaluation_results import auto_submit_review, review_state

router = APIRouter(prefix='/api/knowledge-watches', tags=['knowledge-guided analysis'])
ACTIVE = {'queued', 'running', 'cancelling'}


class CreateWatch(StrictModel):
    run_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)
    workspace_token: str
    interval_minutes: int = Field(default=60, ge=15, le=10080)


class WatchAction(StrictModel):
    expected_revision: int = Field(ge=1)
    workspace_token: str
    action: Literal['pause', 'resume', 'check']


def timestamp(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def check_token(token):
    if token != database.workspace_token():
        raise HTTPException(409, '작업공간이 변경되었습니다. 새로고침해 주세요.')


def checked_watch(db, identity, user):
    row = db.scalar(select(KnowledgeWatch).where(KnowledgeWatch.id == identity,
        KnowledgeWatch.connection_key == database.source_database_key()).with_for_update())
    if not row:
        raise HTTPException(404, '현재 DB에서 지속 분석을 찾을 수 없습니다.')
    actor = live_editor(db, user.id)
    if row.created_by != actor.id and actor.role != 'admin':
        raise HTTPException(403, '본인의 지속 분석만 관리할 수 있습니다.')
    return row


def view(db, row):
    origin = db.get(AnalysisRun, row.origin_run_id)
    last = db.get(AnalysisRun, row.last_run_id) if row.last_run_id else None
    available = True
    try:
        checked_run(db, origin.id, db.get(User, row.created_by))
    except (HTTPException, AttributeError):
        available = False
    return {'id': row.id, 'origin_run_id': row.origin_run_id, 'revision': row.revision, 'project_id': row.project_id, 'enabled': row.enabled,
        'interval_minutes': row.interval_minutes, 'next_check_at': row.next_check_at,
        'checked_at': row.checked_at, 'message': row.message, 'available': available,
        'name': origin.request['_evaluation_dataset']['name'] if available else '분석 범위 확인 필요',
        'case_name': ((origin.request.get('_evaluation_case') or {}).get('name') or '챗에서 시작한 데이터 분석') if available else '',
        'last_run_id': last.id if last and available else None, 'last_status': last.status if last and available else None,
        'review': review_state(db, last) if last and available else None}


@router.get('')
def watches(user=Depends(require_editor)):
    with database.SessionLocal() as db:
        actor = live_editor(db, user.id)
        statement = select(KnowledgeWatch).where(KnowledgeWatch.connection_key == database.source_database_key())
        if actor.role != 'admin':
            statement = statement.where(KnowledgeWatch.created_by == actor.id)
        return [view(db, row) for row in db.scalars(statement.order_by(KnowledgeWatch.created_at.desc()).limit(100))]


@router.post('', status_code=201)
def create(body: CreateWatch, user=Depends(require_editor)):
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        check_token(body.workspace_token)
        run = checked_run(db, body.run_id, user, lock=True)
        existing = db.scalar(select(KnowledgeWatch).where(KnowledgeWatch.created_by == user.id, KnowledgeWatch.origin_run_id == run.id))
        if existing:
            return view(db, existing)
        if run.revision != body.expected_revision:
            raise HTTPException(409, '최신 분석 결과를 다시 확인하세요.')
        if run.status != 'completed' or not run.result or not run.result.get('data_fingerprint'):
            raise HTTPException(422, '현재 버전에서 한 번 분석을 완료한 뒤 지속 분석을 시작하세요.')
        if db.scalar(select(func.count()).select_from(KnowledgeWatch).where(KnowledgeWatch.connection_key == run.connection_key)) >= 30:
            raise HTTPException(422, '현재 DB의 지속 분석은 30개까지 지원합니다.')
        row = KnowledgeWatch(connection_key=run.connection_key, created_by=user.id, origin_run_id=run.id,
            project_id=run.request.get('project_id'), interval_minutes=body.interval_minutes,
            fingerprint=run.result['data_fingerprint'], last_run_id=run.id,
            next_check_at=utcnow() + timedelta(minutes=body.interval_minutes),
            message='선택한 데이터의 추가·수정·삭제를 확인하고, 바뀐 경우 같은 조건으로 재분석합니다.')
        db.add(row)
        db.flush()
        db.add(Audit(user_id=user.id, action='knowledge_watch.create', entity_id=row.id, details={'run_id': run.id}))
        return view(db, row)


@router.post('/{identity}/actions')
def action(identity: str, body: WatchAction, user=Depends(require_editor)):
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        check_token(body.workspace_token)
        row = checked_watch(db, identity, user)
        if row.revision != body.expected_revision:
            raise HTTPException(409, '지속 분석 상태가 바뀌었습니다. 새로고침해 주세요.')
        if body.action != 'pause':
            checked_run(db, row.origin_run_id, db.get(User, row.created_by))
            row.enabled, row.next_check_at = True, utcnow()
            row.message = '새 데이터 확인을 요청했습니다.'
        else:
            row.enabled, row.message = False, '다음 데이터 확인을 일시정지했습니다. 진행 중 작업은 결과 화면에서 취소할 수 있습니다.'
        row.revision += 1
        db.add(Audit(user_id=user.id, action='knowledge_watch.' + body.action, entity_id=row.id, details={}))
        return view(db, row)


def tick():
    # Same lock order as job creation/actions; checks are bounded and normally hourly.
    with database.SessionLocal() as db:
        identities = list(db.scalars(select(KnowledgeWatch.id).where(
            KnowledgeWatch.connection_key == database.source_database_key()).order_by(KnowledgeWatch.next_check_at).limit(30)))
    for identity in identities:
        with database.SessionLocal.begin() as db:
            serialize_creation(db)
            lock_active_workspace(db)
            row = db.scalar(select(KnowledgeWatch).where(KnowledgeWatch.id == identity).with_for_update(skip_locked=True))
            if row is None:
                continue
            try:
                with db.begin_nested():
                    reconcile_and_check(db, row)
            except HTTPException as exc:
                if exc.status_code == 429:
                    continue
                row.enabled, row.message = False, '지속 분석을 멈췄습니다. ' + str(exc.detail)
                row.revision += 1
            except Exception:
                row.enabled, row.message = False, '데이터 확인 중 오류가 발생했습니다. 연결과 분석 범위를 확인한 뒤 재개하세요.'
                row.revision += 1


def reconcile_and_check(db, row):
    actor = live_editor(db, row.created_by)
    last = db.get(AnalysisRun, row.last_run_id) if row.last_run_id else None
    if last and last.retry_run_id:
        last = db.get(AnalysisRun, last.retry_run_id)
        row.last_run_id = last.id
    if last and last.status in ACTIVE:
        return
    if last and row.processed_run_id != last.id:
        if last.status == 'completed':
            checked_run(db, last.id, actor, lock=True)
            review = auto_submit_review(db, last, actor)
            row.fingerprint = last.result.get('data_fingerprint', row.fingerprint)
            row.message = '분석 결과를 확인 요청에 보냈습니다. 승인 후 AI 지식에 반영됩니다.' if review else '분석은 완료했지만 유효한 관측값이 없어 검토 요청을 만들지 않았습니다.'
        else:
            row.enabled, row.message = False, '분석이 실패하거나 취소되어 지속 분석을 멈췄습니다. 결과 화면에서 재시도할 수 있습니다.'
        row.processed_run_id = last.id
        row.revision += 1
    if not row.enabled or timestamp(row.next_check_at) > utcnow():
        return
    origin = checked_run(db, row.origin_run_id, actor)
    from .evaluation_analysis import METHOD_VERSION as STATS_METHOD
    from .evaluation_ml import METHOD_VERSION as ML_METHOD
    expected_method = ML_METHOD if origin.request['settings']['method'] == 'ml_regression' else STATS_METHOD
    baseline_result = origin.result or (last.result if last and last.status == 'completed' else None)
    if not baseline_result:
        raise HTTPException(409, '첫 분석 결과가 아직 없습니다. 실패·취소된 작업을 재시도한 뒤 재개하세요.')
    if baseline_result.get('method_version') != expected_method:
        raise HTTPException(409, '분석 방법이 갱신되었습니다. 새 방법으로 결과를 확인한 뒤 지속 분석을 설정하세요.')
    if origin.request['_workspace_token'] != database.workspace_token():
        raise HTTPException(409, 'DB 연결 설정이 바뀌었습니다. 현재 범위로 분석을 다시 설정하세요.')
    _, cte, params, _ = _compile(Search.model_validate(origin.request['_evaluation_search']), check_types=True)
    observed = data_fingerprint(cte, params)
    # Recheck scope after the source query, and serialize actual queue creation only.
    serialize_creation(db)
    lock_active_workspace(db)
    checked_run(db, origin.id, actor)
    row.checked_at = utcnow()
    row.next_check_at = row.checked_at + timedelta(minutes=row.interval_minutes)
    row.revision += 1
    if observed['fingerprint'] == row.fingerprint:
        row.message = '데이터 변경 없음 · 기존 분석 결과를 유지합니다.'
        return
    _quota(db, actor)
    _quota(db, actor, preparing=True)
    request = deepcopy(origin.request)
    request.pop('_evaluation_review', None)
    request['_knowledge_watch_id'] = row.id
    request['_workspace_token'] = database.workspace_token()
    request['_owner_role'] = actor.role
    request['_knowledge_watch_change'] = {'previous_run_id': row.processed_run_id, 'previous_fingerprint': row.fingerprint,
        'detected_fingerprint': observed['fingerprint'], 'detected_rows': observed['row_count'], 'detected_at': row.checked_at.isoformat()}
    run = AnalysisRun(connection_key=row.connection_key, kind='evaluation', status='queued', request=request,
        source_snapshot=deepcopy(origin.source_snapshot), created_by=actor.id, question_ids=[], comparison_run_id=row.processed_run_id)
    db.add(run)
    db.flush()
    row.last_run_id, row.message = run.id, '데이터 변경을 감지해 통계·분석 작업을 요청했습니다.'
    db.add(Audit(user_id=actor.id, action='knowledge_watch.dispatch', entity_id=row.id, details={'run_id': run.id}))
