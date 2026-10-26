"""Read stored results only; never query the measurement source or re-run work."""
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import defer

from .. import db as database
from ..auth import require_editor
from .history_contract import HistoryRun, ScheduleHistory, ScheduleComparison, SeriesComparison, SeriesEvidence
from .models import AnalysisRun
from .proposals import live_editor
from .schedules import DATES, owned_schedule, public_request, utc
from .service import find_run

router = APIRouter(prefix='/api/analytics/schedules', tags=['analysis history'])
PAGE_SIZE = 20


def summary(run, schedule):
    start, end = utc(run.started_at), utc(run.completed_at)
    return HistoryRun(**{k: getattr(run, k) for k in (
        'id', 'status', 'retry_of', 'retry_run_id', 'comparison_run_id')},
        created_at=utc(run.created_at), completed_at=end, scheduled_for=utc(run.scheduled_for),
        duration_seconds=max(0, (end - start).total_seconds()) if start and end else None,
        question_count=len(run.question_ids), is_template=run.id == schedule.origin_run_id,
        queried_at=(run.source_watermark or {}).get('captured_at'))


def history_query(schedule):
    return select(AnalysisRun).options(defer(AnalysisRun.result, raiseload=True)).where(
        AnalysisRun.connection_key == schedule.connection_key,
        or_(AnalysisRun.schedule_id == schedule.id, AnalysisRun.id == schedule.origin_run_id))


@router.get('/{identity}/history', response_model=ScheduleHistory)
def history(identity: str, before: str | None = Query(None, max_length=36), user=Depends(require_editor)):
    with database.SessionLocal() as db:
        schedule = owned_schedule(db, identity, live_editor(db, user.id))
        query = history_query(schedule)
        if before:
            cursor = db.scalar(history_query(schedule).where(AnalysisRun.id == before))
            if cursor is None:
                raise HTTPException(422, '이 일정의 이력에서 다시 조회하세요.')
            query = query.where(or_(AnalysisRun.created_at < cursor.created_at,
                and_(AnalysisRun.created_at == cursor.created_at, AnalysisRun.id < cursor.id)))
        rows = list(db.scalars(query.order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).limit(PAGE_SIZE + 1)))
        items, hidden = [], 0
        for run in rows[:PAGE_SIZE]:
            try:
                # Same source and project visibility as the result screen; no result body access.
                find_run(db, run.id)
            except HTTPException:
                hidden += 1
                continue
            items.append(summary(run, schedule))
        return ScheduleHistory(workspace_token=database.workspace_token(), schedule_id=schedule.id,
            items=items, hidden_count=hidden, next_cursor=rows[PAGE_SIZE-1].id if len(rows) > PAGE_SIZE else None)


def condition_signature(run):
    request = public_request(run.request)
    values = request.model_dump(mode='json', exclude=set(DATES))
    values['_project_scope'] = run.request.get('_project_scope')
    anchor = request.current_end if request.kind == 'trend' else request.end
    # Different calendar periods are expected, but their lengths/gap must match.
    values['windows'] = {k: (getattr(request, k) - anchor).total_seconds()
                         if anchor and getattr(request, k) else None for k in DATES}
    return values


def comparison_reasons(previous, current):
    reasons = []
    if not previous:
        return ['이 실행에는 이전 비교 대상이 기록되지 않았습니다. 이전 버전의 이력은 비교 대상을 추정하지 않습니다.']
    if any(r.status != 'completed' or r.result is None for r in (previous, current)):
        reasons.append('두 실행이 모두 완료된 결과를 가진 경우에만 수치와 판정을 비교합니다.')
    if condition_signature(previous) != condition_signature(current):
        reasons.append('분석 대상·기간 길이·판정 기준·승인 근거 또는 프로젝트 범위가 서로 다릅니다.')
    if previous.source_snapshot != current.source_snapshot:
        reasons.append('두 실행의 원천 정의가 다릅니다.')
    methods = [(r.result or {}).get('method_version') for r in (previous, current)]
    if not all(methods) or methods[0] != methods[1]:
        reasons.append('분석 방법 버전이 다르거나 과거 결과에 기록되지 않았습니다.')
    return reasons


def finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def evidence(series, kind):
    a = series.get({'trend': 'baseline', 'split': 'group_a', 'statistics': 'statistics'}[kind]) or {}
    b = (series.get('current' if kind == 'trend' else 'group_b') or {}) if kind != 'statistics' else {}
    criteria = series.get('criteria') or {}
    return SeriesEvidence(status=series.get('status'), samples_a=a.get('valid_count'), mean_a=finite(a.get('mean')),
        samples_b=b.get('valid_count'), mean_b=finite(b.get('mean')), delta=finite(series.get('delta')),
        min_samples=criteria.get('min_samples'), absolute_delta=finite(criteria.get('absolute_delta')))


def direction(value):
    return (1 if (value.delta or 0) > 0 else -1) if value.status == 'changed' else 0


def compare_rows(previous, current, comparable):
    def mapped(run):
        return {(s['family'], s.get('operation') or '', s['parameter']): evidence(s, run.kind)
                for s in (run.result or {}).get('series', [])} if run else {}
    old, new = mapped(previous), mapped(current)
    # The scheduler sends a criteria-check notice instead of a change notice if
    # any new series has no confirmed rule, even when other series changed.
    alerts_enabled = current.kind != 'statistics' and not any(s.status == 'needs_criteria' for s in new.values())
    rows = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        if not comparable: change = 'not_comparable'
        elif a is None: change = 'added'
        elif b is None: change = 'removed'
        elif a.status != b.status: change = 'state'
        elif direction(a) != direction(b): change = 'direction'
        elif a != b: change = 'values'
        else: change = 'unchanged'
        rows.append(SeriesComparison(family=key[0], operation=key[1], parameter=key[2], previous=a, current=b,
            change=change, notification_change=(alerts_enabled and change in {'added', 'removed', 'state', 'direction'}) if comparable else None))
    return rows


@router.get('/{identity}/runs/{run_id}/comparison', response_model=ScheduleComparison)
def comparison(identity: str, run_id: str, offset: int = Query(0, ge=0, le=10000),
               differences_only: bool = True, user=Depends(require_editor)):
    with database.SessionLocal() as db:
        schedule = owned_schedule(db, identity, live_editor(db, user.id))
        current = find_run(db, run_id)
        if current.schedule_id != schedule.id:
            raise HTTPException(404, '이 일정에 연결된 실행을 선택하세요.')
        # A pinned template may no longer be the current template after an edit.
        previous, unavailable = None, []
        if current.comparison_run_id:
            try:
                previous = find_run(db, current.comparison_run_id)
            except HTTPException:
                unavailable = ['이전 결과의 원천·프로젝트 범위가 변경되어 현재 권한 범위에서 비교할 수 없습니다.']
        reasons = unavailable or comparison_reasons(previous, current)
        comparable = not reasons
        rows = compare_rows(previous, current, comparable)
        changed = sum(r.change != 'unchanged' for r in rows) if comparable else None
        alerts = sum(bool(r.notification_change) for r in rows) if comparable else None
        selected = [r for r in rows if not differences_only or r.change != 'unchanged']
        return ScheduleComparison(workspace_token=database.workspace_token(), schedule_id=schedule.id,
            current=summary(current, schedule), previous=summary(previous, schedule) if previous else None,
            current_request=public_request(current.request), previous_request=public_request(previous.request) if previous else None,
            comparable=comparable, reasons=reasons, total_series=len(rows), changed_count=changed,
            notification_change_count=alerts, matched_count=len(selected), offset=offset,
            has_more=offset+100 < len(selected), rows=selected[offset:offset+100])
