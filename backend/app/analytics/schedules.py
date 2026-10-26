"""Durable schedules share the normal analysis queue, limits, evidence and controls."""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import defer

from .. import db as database
from ..auth import require_editor
from ..models import Audit, User, utcnow
from ..semantic.collector_service import approved_knowledge
from .data import selected_snapshot, source_snapshot, validate_snapshot
from .models import AnalysisNotification, AnalysisRun, AnalysisSchedule
from .proposals import live_editor, lock_active_workspace
from .schedule_contract import (AnalysisNotice, NoticeAck, ScheduleAction, ScheduleList,
    SchedulePreview, SchedulePreviewRequest, ScheduleSave, ScheduleView)
from .schemas import AnalysisRequest
from .service import METHOD_VERSION, create_run, find_run, project_scope, serialize_creation

router = APIRouter(prefix='/api/analytics/schedules', tags=['analysis schedules'])
ACTIVE = {'queued', 'running', 'cancelling'}
DATES = ('start', 'end', 'baseline_start', 'baseline_end', 'current_start', 'current_end')
NOTES = [
    '확정한 기간의 길이와 간격을 유지하고, 마지막 종료 시각을 각 예약 시각으로 이동합니다. 시간 간격은 UTC 기준입니다.',
    '앱이 실행되고 이 DB가 선택되어 있을 때 실행합니다. 놓친 일정은 최신 1회만 실행하며 같은 일정의 작업을 겹쳐 실행하지 않습니다.',
    '실패·취소·기준 확인 필요 시 일시정지합니다. 결과의 질문·답변·승인은 그대로 이어집니다.',
    '변화 알림은 PARA별 판정 상태·변화 방향·표본 부족 상태가 달라질 때 표시합니다. 같은 판정의 반복과 단순 수치 변동은 알리지 않습니다.',
    '기본 통계는 변화 판정을 하지 않으며 실행 실패·확인 필요만 알립니다. 알림은 앱의 작업 상태에 저장됩니다.',
]


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def public_request(stored):
    return AnalysisRequest.model_validate({k: v for k, v in stored.items() if not k.startswith('_')})


def rolling_request(stored, due):
    request = public_request(stored)
    anchor = request.current_end if request.kind == 'trend' else request.end
    if anchor is None:
        raise HTTPException(422, '정기 분석에는 시작·종료 기간이 확정된 분석이 필요합니다.')
    shift = utc(due) - anchor
    values = request.model_dump(mode='json')
    for field in DATES:
        value = getattr(request, field)
        if value is not None:
            values[field] = (value + shift).isoformat()
    return AnalysisRequest.model_validate(values)


def outcome(result):
    return sorted([[s['family'], s.get('operation') or '', s['parameter'], s.get('status', ''),
                    (1 if s.get('delta', 0) > 0 else -1) if s.get('status') == 'changed' else 0]
                   for s in (result or {}).get('series', [])])


def check_token(token):
    if token != database.workspace_token():
        raise HTTPException(409, '작업공간이 변경되었습니다. 현재 DB에서 다시 확인하세요.')


def checked_template(db, identity):
    run = find_run(db, identity)
    if run.kind == 'evaluation':
        raise HTTPException(422, '사례 기반 분석은 현재 데이터와 조건을 확인한 뒤 채팅에서 실행하세요.')
    request = public_request(run.request)
    if (run.result or {}).get('method_version', METHOD_VERSION) != METHOD_VERSION:
        raise HTTPException(409, '분석 방법이 변경되었습니다. 현재 방법으로 완료한 분석을 사용하세요.')
    if (run.status != 'completed' or not run.result or request.purpose != 'analysis'
            or request.missing() or request.missing_criteria()
            or any(s.get('status') == 'needs_criteria' for s in run.result.get('series', []))):
        raise HTTPException(422, '조건·판정 기준을 확정하고 완료한 업무 분석에서 정기 분석을 설정하세요.')
    rolling_request(run.request, utcnow())
    check_context(db, run.request, run.source_snapshot, METHOD_VERSION)
    return run


def check_context(db, stored, snapshot, method):
    request = public_request(stored)
    if method != METHOD_VERSION:
        raise HTTPException(409, '분석 방법이 변경되었습니다. 새 분석 결과의 조건을 적용하세요.')
    if stored.get('_project_scope') != project_scope(db, request.project_id):
        raise HTTPException(409, '프로젝트 범위가 변경되었습니다. 현재 범위의 새 분석 조건을 적용하세요.')
    validate_snapshot(db, snapshot)
    current, _ = source_snapshot(db)
    if selected_snapshot(current, request) != snapshot:
        raise HTTPException(409, '분석 원천이 변경되었습니다. 현재 원천의 새 분석 조건을 적용하세요.')
    approved = {(item['id'], item['version']) for item in approved_knowledge(db)}
    if any((ref.item_id, ref.version) not in approved for ref in request.knowledge_refs):
        raise HTTPException(409, '연결된 승인 지식이 변경되었습니다. 현재 승인 근거를 확인한 분석 조건을 적용하세요.')


def owned_schedule(db, identity, actor):
    schedule = db.get(AnalysisSchedule, identity)
    if (not schedule or schedule.connection_key != database.source_database_key()
            or (schedule.created_by != actor.id and actor.role != 'admin')):
        raise HTTPException(404, '현재 작업공간에서 관리할 정기 분석을 찾을 수 없습니다.')
    return schedule


def view(schedule):
    return ScheduleView(**{k: getattr(schedule, k) for k in (
        'id', 'name', 'project_id', 'origin_run_id', 'interval_hours', 'enabled', 'revision',
        'last_run_id', 'message')}, next_run_at=utc(schedule.next_run_at), can_manage=True,
        kind=schedule.request['kind'], request=public_request(schedule.request))


def schedule_query(actor):
    query = select(AnalysisSchedule).where(AnalysisSchedule.connection_key == database.source_database_key())
    return query if actor.role == 'admin' else query.where(AnalysisSchedule.created_by == actor.id)


@router.get('', response_model=ScheduleList)
def list_schedules(project_id: int | None = None, user=Depends(require_editor)):
    with database.SessionLocal() as db:
        actor = live_editor(db, user.id)
        query = schedule_query(actor)
        if project_id is not None:
            query = query.where(AnalysisSchedule.project_id == project_id)
        rows = list(db.scalars(query.order_by(AnalysisSchedule.created_at.desc()).limit(100)))
        schedules = {s.id: s for s in rows}
        notices_query = select(AnalysisNotification).where(AnalysisNotification.schedule_id.in_(schedules))
        unread = db.scalar(select(func.count()).select_from(AnalysisNotification).where(
            AnalysisNotification.schedule_id.in_(schedules), AnalysisNotification.acknowledged_at.is_(None)))
        notices = list(db.scalars(notices_query.order_by(AnalysisNotification.acknowledged_at.is_not(None),
            AnalysisNotification.created_at.desc(), AnalysisNotification.id).limit(51)))
        return ScheduleList(workspace_token=database.workspace_token(), items=[view(s) for s in rows],
            unread_count=unread, has_more_notifications=len(notices) > 50,
            notifications=[AnalysisNotice(**{k: getattr(n, k) for k in ('id', 'schedule_id', 'kind', 'message', 'run_id', 'comparison_run_id')},
                created_at=utc(n.created_at), acknowledged_at=utc(n.acknowledged_at),
                schedule_name=schedules[n.schedule_id].name, project_id=schedules[n.schedule_id].project_id) for n in notices[:50]])


@router.post('/preview', response_model=SchedulePreview)
def preview(body: SchedulePreviewRequest, user=Depends(require_editor)):
    check_token(body.workspace_token)
    with database.SessionLocal() as db:
        live_editor(db, user.id)
        run = checked_template(db, body.source_run_id)
        first = utcnow() + timedelta(hours=body.interval_hours)
        return SchedulePreview(source_run_id=run.id, source_run_revision=run.revision,
            interval_hours=body.interval_hours,
            workspace_token=database.workspace_token(), first_run_at=first,
            request=rolling_request(run.request, first), notes=NOTES)


def save_template(db, schedule, body):
    check_token(body.workspace_token)
    run = checked_template(db, body.source_run_id)
    if run.revision != body.expected_run_revision:
        raise HTTPException(409, '분석 기록이 변경되었습니다. 정기 분석 조건을 다시 확인하세요.')
    if body.first_run_at.tzinfo is None or not (utcnow() < body.first_run_at <= utcnow() + timedelta(hours=body.interval_hours, minutes=5)):
        raise HTTPException(409, '첫 실행 시각을 다시 미리보기한 뒤 저장하세요.')
    if not body.name.strip():
        raise HTTPException(422, '정기 분석 이름을 입력하세요.')
    schedule.name, schedule.origin_run_id = body.name.strip(), run.id
    schedule.request, schedule.source_snapshot = dict(run.request), dict(run.source_snapshot)
    schedule.project_id, schedule.method_version = run.request.get('project_id'), METHOD_VERSION
    schedule.interval_hours, schedule.next_run_at = body.interval_hours, body.first_run_at
    schedule.outcome, schedule.message = outcome(run.result), '확정한 조건으로 다음 실행을 기다립니다.'
    schedule.outcome_run_id = run.id
    schedule.enabled, schedule.updated_at = True, utcnow()


@router.post('', response_model=ScheduleView, status_code=201)
def create(body: ScheduleSave, user=Depends(require_editor)):
    check_token(body.workspace_token)
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        actor = live_editor(db, user.id)
        previous = db.scalar(select(AnalysisSchedule).where(AnalysisSchedule.created_by == actor.id,
            AnalysisSchedule.origin_run_id == body.source_run_id))
        if previous:
            if previous.name != body.name.strip() or previous.interval_hours != body.interval_hours:
                raise HTTPException(409, '이 분석으로 만든 일정이 있습니다. 작업 상태에서 기존 일정을 확인하세요.')
            return view(previous)
        if db.scalar(select(func.count()).select_from(AnalysisSchedule).where(AnalysisSchedule.connection_key == database.source_database_key())) >= 100:
            raise HTTPException(429, '이 DB에는 정기 분석을 100개까지 등록할 수 있습니다. 기존 일정의 조건을 변경해 주세요.')
        schedule = AnalysisSchedule(connection_key=database.source_database_key(), created_by=actor.id)
        save_template(db, schedule, body)
        db.add(schedule)
        db.flush()
        db.add(Audit(user_id=actor.id, action='analytics.schedule.create', entity_id=schedule.id, details={'source_run_id': body.source_run_id}))
        return view(schedule)


@router.post('/{identity}/actions', response_model=ScheduleView)
def action(identity: str, body: ScheduleAction, user=Depends(require_editor)):
    check_token(body.workspace_token)
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        actor = live_editor(db, user.id)
        schedule = owned_schedule(db, identity, actor)
        if schedule.revision != body.expected_revision:
            raise HTTPException(409, '정기 분석 설정이 변경되었습니다. 최신 상태를 확인하세요.')
        last = db.get(AnalysisRun, schedule.last_run_id) if schedule.last_run_id else None
        if body.action == 'pause':
            schedule.enabled, schedule.next_run_at = False, None
            schedule.message = '다음 예약을 일시정지했습니다. 이미 시작한 작업은 결과 화면에서 별도로 취소할 수 있습니다.'
        else:
            if last and last.status in ACTIVE:
                raise HTTPException(409, '진행 중인 작업을 완료하거나 취소한 뒤 예약을 재개하세요.')
            # Consume a finished run before the action so stale completion cannot undo a resumed/replaced schedule.
            if last:
                reconcile(db, schedule, last)
            if body.action == 'replace':
                live_editor(db, schedule.created_by)
                if not body.replacement:
                    raise HTTPException(422, '새로 완료한 분석의 조건 미리보기가 필요합니다.')
                replacement = checked_template(db, body.replacement.source_run_id)
                if replacement.kind != schedule.request['kind'] or replacement.request.get('project_id') != schedule.project_id:
                    raise HTTPException(422, '같은 분석 종류·프로젝트의 완료된 조건을 적용하세요.')
                duplicate = db.scalar(select(AnalysisSchedule.id).where(AnalysisSchedule.created_by == schedule.created_by,
                    AnalysisSchedule.origin_run_id == replacement.id, AnalysisSchedule.id != schedule.id))
                if duplicate:
                    raise HTTPException(409, '선택한 분석은 이미 다른 일정에 연결되어 있습니다.')
                save_template(db, schedule, body.replacement)
            else:
                if last and (last.status == 'needs_input' or any(s.get('status') == 'needs_criteria' for s in (last.result or {}).get('series', []))):
                    raise HTTPException(409, '연결된 질문의 기준을 확인하고 완료한 새 분석 조건을 적용하세요.')
                live_editor(db, schedule.created_by)
                check_context(db, schedule.request, schedule.source_snapshot, schedule.method_version)
                schedule.enabled = True
                schedule.next_run_at = utcnow() + timedelta(hours=schedule.interval_hours)
                schedule.message = '다음 예약부터 같은 조건으로 실행합니다.'
        schedule.revision += 1
        schedule.updated_at = utcnow()
        db.add(Audit(user_id=actor.id, action='analytics.schedule.' + body.action, entity_id=schedule.id, details={}))
        return view(schedule)


@router.post('/notifications/{identity}/ack')
def acknowledge(identity: str, body: NoticeAck, user=Depends(require_editor)):
    check_token(body.workspace_token)
    with database.SessionLocal.begin() as db:
        actor = live_editor(db, user.id)
        notice = db.get(AnalysisNotification, identity)
        if not notice:
            raise HTTPException(404, '알림을 찾을 수 없습니다.')
        owned_schedule(db, notice.schedule_id, actor)
        notice.acknowledged_at = notice.acknowledged_at or utcnow()
        return {'acknowledged': True}


def notify(db, schedule, kind, message, run=None):
    key = f'{run.id if run else schedule.revision}:{kind}'
    if not db.scalar(select(AnalysisNotification.id).where(AnalysisNotification.schedule_id == schedule.id,
            AnalysisNotification.event_key == key)):
        db.add(AnalysisNotification(schedule_id=schedule.id, event_key=key, kind=kind,
            message=message, run_id=run.id if run else None,
            comparison_run_id=run.comparison_run_id if run else None))


def pause_with_notice(db, schedule, kind, message, run=None):
    notify(db, schedule, kind, message, run)
    schedule.enabled, schedule.next_run_at, schedule.message = False, None, message
    schedule.revision += 1
    schedule.updated_at = utcnow()


def reconcile(db, schedule, run):
    if schedule.processed_run_id == run.id or run.status in ACTIVE:
        return
    schedule.processed_run_id = run.id
    if run.status in {'failed', 'cancelled', 'needs_input'}:
        label = {'failed': '분석 실패', 'cancelled': '작업 취소', 'needs_input': '기준 확인 필요'}[run.status]
        pause_with_notice(db, schedule, run.status, label + '로 정기 분석을 일시정지했습니다. 연결된 결과를 확인하세요.', run)
    elif run.status == 'completed':
        # Keep the exact evidence pair, even after a later template replacement.
        run.comparison_run_id = schedule.outcome_run_id
        values = outcome(run.result)
        if any(value[3] == 'needs_criteria' for value in values):
            pause_with_notice(db, schedule, 'needs_input', '새 PARA의 판정 기준 확인이 필요합니다. 질문·승인 후 완료한 분석 조건을 적용하세요.', run)
        elif run.kind != 'statistics' and values != schedule.outcome:
            counts = {state: sum(v[3] == state for v in values) for state in ('changed', 'stable', 'insufficient_data')}
            message = (f"이전 결과와 판정 상태가 달라졌습니다. 변화 감지 {counts['changed']}개 · 기준 미만 {counts['stable']}개 · 표본 부족 {counts['insufficient_data']}개."
                       if values else '분석 대상 측정 계열이 없습니다. 이전 결과와 데이터 범위가 달라졌습니다.')
            notify(db, schedule, 'changed', message, run)
        schedule.outcome = values
        schedule.outcome_run_id = run.id
    schedule.updated_at = utcnow()


def scheduler_tick():
    """One global creation lock prevents duplicate slots across app processes/retries."""
    queued = False
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        rows = list(db.scalars(select(AnalysisSchedule).where(
            AnalysisSchedule.connection_key == database.source_database_key(),
            or_(AnalysisSchedule.enabled.is_(True), AnalysisSchedule.processed_run_id != AnalysisSchedule.last_run_id,
                (AnalysisSchedule.processed_run_id.is_(None) & AnalysisSchedule.last_run_id.is_not(None))))
            .order_by(AnalysisSchedule.next_run_at, AnalysisSchedule.id).limit(100)))
        for schedule in rows:
            last = db.scalar(select(AnalysisRun).options(defer(AnalysisRun.result)).where(
                AnalysisRun.id == schedule.last_run_id)) if schedule.last_run_id else None
            if last:
                reconcile(db, schedule, last)
            now = utcnow()
            if not schedule.enabled or not schedule.next_run_at or utc(schedule.next_run_at) > now or last and last.status in ACTIVE:
                continue
            try:
                # Savepoint rolls back any generated questions/run if validation fails late.
                with db.begin_nested():
                    actor = live_editor(db, schedule.created_by)
                    check_context(db, schedule.request, schedule.source_snapshot, schedule.method_version)
                    interval = timedelta(hours=schedule.interval_hours)
                    due = utc(schedule.next_run_at)
                    due += ((now - due) // interval) * interval
                    run = create_run(db, rolling_request(schedule.request, due), actor)
                    if run.source_snapshot != schedule.source_snapshot or run.request.get('_project_scope') != schedule.request.get('_project_scope'):
                        raise HTTPException(409, '분석 범위가 변경되었습니다. 새 분석 조건을 적용하세요.')
                    run.schedule_id, run.scheduled_for = schedule.id, due
                    run.comparison_run_id = schedule.outcome_run_id
                    schedule.last_run_id, schedule.next_run_at = run.id, due + interval
                    schedule.revision += 1
                    schedule.message, schedule.updated_at = '예약한 조건으로 분석을 요청했습니다.', now
                    queued = queued or run.status == 'queued'
                    db.add(Audit(user_id=actor.id, action='analytics.schedule.dispatch', entity_id=schedule.id,
                        details={'run_id': run.id, 'scheduled_for': due.isoformat()}))
            except HTTPException as error:
                if error.status_code == 429:
                    # Keep the slot due; normal queue limits apply without generating failed jobs or notices.
                    continue
                pause_with_notice(db, schedule, 'blocked', '정기 분석을 일시정지했습니다. ' + str(error.detail))
    if queued:
        from .worker import wake_worker
        wake_worker()


def tick_active_workspace():
    from ..database_settings import load_active_source
    binding = load_active_source()
    with database.use_source(binding):
        scheduler_tick()
        from ..knowledge_watch import tick as knowledge_watch_tick
        knowledge_watch_tick()


@asynccontextmanager
async def schedule_worker():
    from .. import worker_observation
    worker_observation.start('schedule')
    async def loop():
        while True:
            try:
                await asyncio.to_thread(worker_observation.run, 'schedule', tick_active_workspace)
            except Exception:
                logging.getLogger(__name__).warning('Analysis schedule check unavailable.')
            await asyncio.sleep(15)
    task = asyncio.create_task(loop(), name='analysis-schedules')
    try:
        yield
    finally:
        worker_observation.stop('schedule')
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
