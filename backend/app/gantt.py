"""Bounded WIP schedules; source writes and invented actual timestamps are forbidden."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from fastapi import HTTPException
from sqlalchemy import select

from .db import SessionLocal
from .models import Source
from .expressions import quote
from .table_visibility import require_source_visible

PLAN_COLUMNS = {'lot_cd', 'lot_id', 'operation_seq', 'operation'}
JOINED_COLUMNS = PLAN_COLUMNS | {'current_operation', 'current_started_at', 'remark',
    'completed_at', 'history_count', 'expected_hours', 'tat_samples', 'tat_basis'}
SOURCE_COLUMNS = {
    'status': {'lot_cd', 'lot_id', 'operation', 'remark'},
    'tat': {'lot_cd', 'lot_id', 'operation', 'tat'},
    'history': {'lot_cd', 'lot_id', 'operation', 'end_dt_tm'},
}


def validate_gantt(card, compiled, catalog=None):
    if not card.gantt:
        raise HTTPException(422, '간트차트의 현재 상태·TAT·이력 테이블을 선택하세요.')
    if not PLAN_COLUMNS.issubset({c['name'] for c in compiled['columns']}):
        raise HTTPException(422, '공정 계획 데이터셋에 lot_cd, lot_id, operation_seq, operation이 필요합니다.')
    if card.trellis or card.value_expression or card.aggregate != 'none':
        raise HTTPException(422, '간트차트는 랏·공정 단위로 표시합니다. 집계식과 패널 구분은 사용할 수 없습니다.')
    if any(f.column not in {'lot_cd', 'lot_id'} for f in card.filters):
        raise HTTPException(422, '간트차트 필터에는 lot_cd 또는 lot_id를 선택하세요.')
    if card.gantt.data_mode == 'dataset':
        from .query import _filter_sql
        columns = {c['name']: c['type'] for c in compiled['columns']}
        missing = JOINED_COLUMNS - columns.keys()
        if missing:
            raise HTTPException(422, '일정 데이터셋에 필요한 출력 컬럼: ' + ', '.join(sorted(missing)))
        for name in ('operation_seq', 'history_count', 'expected_hours', 'tat_samples'):
            if not any(t in columns[name].lower() for t in ('numeric', 'int', 'decimal', 'real', 'double', 'float')):
                raise HTTPException(422, f'{name}은 숫자 컬럼이어야 합니다. expected_hours의 단위는 시간입니다.')
        for name in ('current_started_at', 'completed_at'):
            if not any(t in columns[name].lower() for t in ('timestamp', 'date')):
                raise HTTPException(422, f'{name}은 날짜/시각 컬럼이어야 합니다.')
        for prefix, filters in (('line', card.gantt.line_filters), ('key', card.gantt.key_filters), ('lot', card.filters)):
            _filter_sql(filters, columns, {}, prefix)
        return compiled
    if card.gantt.line_filters or card.gantt.key_filters:
        raise HTTPException(422, '표시 필터를 사용하려면 먼저 조인 SQL 데이터셋을 만드세요.')
    if catalog is None:
        with SessionLocal() as db:
            sources = {s.id: s for s in db.scalars(select(Source))}
    else:
        sources = catalog.sources
    chosen = {}
    plan = sources.get(compiled['source']['id'])
    if plan is None or not PLAN_COLUMNS.issubset({c['name'] for c in plan.columns}):
        raise HTTPException(422, '표시 데이터셋의 원천 테이블에는 전체 공정 계획이 필요합니다: lot_cd, lot_id, operation_seq, operation.')
    require_source_visible(plan)
    chosen['plan'] = plan
    for role, required in SOURCE_COLUMNS.items():
        source = sources.get(getattr(card.gantt, role + '_source_id'))
        if source is None:
            raise HTTPException(404, '일정에 연결된 원천 테이블을 찾을 수 없습니다.')
        require_source_visible(source)
        columns = {c['name']: c['type'] for c in source.columns}
        if not required.issubset(columns):
            raise HTTPException(422, f'{source.table_name}: 필요한 컬럼은 {", ".join(sorted(required))}입니다.')
        if role == 'tat' and not any(t in columns['tat'].lower() for t in ('numeric', 'int', 'decimal', 'real', 'double')):
            raise HTTPException(422, 'tat는 시간(hour) 단위의 숫자 컬럼이어야 합니다.')
        if role == 'history' and not any(t in columns['end_dt_tm'].lower() for t in ('timestamp', 'date')):
            raise HTTPException(422, 'end_dt_tm은 날짜/시각 컬럼이어야 합니다.')
        if role == 'status' and card.gantt.started_column:
            if not any(t in columns.get(card.gantt.started_column, '').lower() for t in ('timestamp', 'date')):
                raise HTTPException(422, '현재 공정 시작 시각 컬럼을 확인하세요.')
        chosen[role] = source
    return dict(compiled, gantt_sources=chosen)


def as_time(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def schedule_segments(rows, as_of):
    """Pure calculation: unknown starts/overruns block downstream predictions."""
    by_lot = defaultdict(list)
    for row in rows:
        by_lot[row['lot_cd'], row['lot_id']].append(row)
    result = []
    for (lot_cd, lot_id), route in by_lot.items():
        route.sort(key=lambda r: r['operation_seq'])
        seqs = [r['operation_seq'] for r in route]
        operations = [r['operation'] for r in route]
        if len(set(seqs)) != len(seqs) or len(set(operations)) != len(operations) or any(r['history_count'] > 1 for r in route):
            raise HTTPException(422, f'{lot_id}: 반복 공정은 이력·TAT·현재 상태에도 operation_seq가 필요합니다.')
        matches = [r for r in route if r['operation'] == r['current_operation']]
        if len(matches) != 1:
            raise HTTPException(422, f'{lot_id}: 현재 공정과 공정 계획을 연결할 수 없습니다.')
        current_seq = matches[0]['operation_seq']
        previous_end = None
        forecast_end = None
        for row in route:
            seq = row['operation_seq']
            status = 'completed' if seq < current_seq else 'current' if seq == current_seq else 'upcoming'
            end = as_time(row['completed_at'])
            expected = float(row['expected_hours']) if row['expected_hours'] is not None else None
            if expected is not None and (expected <= 0 or expected > 876000):
                expected = None
            start = None
            estimated = True
            issue = None
            overrun = False
            actual_end = None
            if status == 'completed':
                if end is None or end > as_of or (previous_end and end < previous_end):
                    raise HTTPException(422, f'{lot_id}: 완료 이력의 순서·시각을 확인하세요.')
                start = previous_end
                actual_end = end
                previous_end = end
                if start is None:
                    issue = '시작 시각 없음'
            elif status == 'current':
                if end is not None:
                    raise HTTPException(422, f'{lot_id}: 현재 공정이 완료 이력에도 있습니다. 기준 시각과 상태를 확인하세요.')
                explicit_start = as_time(row['current_started_at'])
                start = explicit_start or previous_end
                estimated = explicit_start is None
                if start and start > as_of:
                    raise HTTPException(422, f'{lot_id}: 현재 공정 시작이 기준 시각보다 늦습니다.')
                end = as_of
                if not start:
                    issue = '현재 공정 시작 시각 없음'
                elif expected is None:
                    issue = 'TAT 표본 없음'
                else:
                    predicted = start + timedelta(hours=expected)
                    overrun = predicted <= as_of
                    if overrun:
                        issue = '기준 소요 초과 · 완료 시점 미정'
                    else:
                        forecast_end = predicted
                if estimated and not issue:
                    issue = '이전 공정 완료 기준 추정 · 대기 포함'
            else:
                if end is not None:
                    raise HTTPException(422, f'{lot_id}: 현재 공정보다 뒤의 완료 이력이 있습니다.')
                start = forecast_end
                if start and expected is not None:
                    end = start + timedelta(hours=expected)
                    forecast_end = end
                else:
                    end = None
                    forecast_end = None
                    issue = '예상 시점 미정' if expected is not None else 'TAT 표본 없음'
            result.append(dict(lot_cd=lot_cd, lot_id=lot_id, operation=row['operation'], operation_seq=seq,
                               main_oper_yn=str(row.get('main_oper_yn', True)).lower() in ('y', 'true', '1'),
                               displayed=row.get('displayed', True),
                               key_operation=row.get('key_operation', False),
                               status=status, start_at=start, end_at=end, actual_completed_at=actual_end,
                               current_expected_end=forecast_end if status == 'current' else None,
                               expected_hours=expected, tat_samples=row['tat_samples'], tat_basis=row['tat_basis'],
                               estimated=estimated, overrun=overrun, issue=issue, remark=row['remark']))
    return result


def display_segments(segments, *, presentation_filters=False):
    """Apply the display dataset only AFTER scheduling all non-skipped steps."""
    routes = defaultdict(list)
    for row in segments:
        routes[row['lot_cd'], row['lot_id']].append(row)
    show = lambda r: r.get('displayed', True) or (presentation_filters and r.get('key_operation', False))
    summaries = []
    result = []
    for (lot_cd, lot_id), route in routes.items():
        completed = sum(r['status'] == 'completed' for r in route)
        last = route[-1]
        # Preserve the full non-skipped plan endpoint before hiding any steps.
        # A current step's observation time is not its predicted completion.
        plan_end = last['current_expected_end'] if last['status'] == 'current' else last['end_at']
        summaries.append(dict(lot_cd=lot_cd,lot_id=lot_id,total_steps=len(route),completed_steps=completed,
                              main_steps=sum(bool(r['main_oper_yn']) for r in route),displayed_steps=sum(show(r) for r in route),
                              plan_end_at=plan_end,
                              current_operation=next((r['operation'] for r in route if r['status']=='current'),'')))
        if presentation_filters:
            # Ribbon geometry is independent of both presentation filters. Even
            # zero matching markers must retain the full, known schedule.
            bands = []
            for row in route:
                start = row['start_at']
                end = row['current_expected_end'] or row['end_at']
                if start is None or end is None or end <= start:
                    continue
                if bands and bands[-1]['status'] == row['status'] and start <= bands[-1]['end']:
                    bands[-1]['end'] = max(bands[-1]['end'], end)
                else:
                    bands.append(dict(start=start, end=end, status=row['status']))
            summaries[-1].update(ribbon=bands, line_steps=sum(r['displayed'] for r in route),
                                 key_steps=sum(r['key_operation'] for r in route))
        # Expected TAT belongs to the displayed milestone interval, including
        # hidden following steps, rather than observed elapsed time or waits.
        interval = None
        for row in route:
            if show(row):
                interval = dict(row, interval_expected_hours=0.0)
                result.append(interval)
            if interval is not None:
                subtotal, expected = interval['interval_expected_hours'], row['expected_hours']
                interval['interval_expected_hours'] = subtotal + expected if subtotal is not None and expected is not None else None
        if presentation_filters and not any(show(r) for r in route):
            result.append(dict(next(r for r in route if r['status'] == 'current')))
    return result, summaries


def compute_gantt(card, context, compiled):
    if card.gantt.data_mode == 'dataset':
        return compute_dataset_gantt(card, context, compiled)
    from .query import _dataset_cte, _filter_sql, execute_rows, clean
    from .project_settings import managed_project
    from .schemas import Filter
    extra = list(card.filters)
    if context.gantt_lot_cd:
        extra.append(Filter(column='lot_cd', op='eq', value=context.gantt_lot_cd))
    cte, params, ignored = _dataset_cte(compiled, context.model_copy(update={'filters': []}), extra)
    sources = compiled['gantt_sources']
    tables = {role: f'{quote(s.schema_name)}.{quote(s.table_name)}' for role, s in sources.items()}
    start_col = f's.{quote(card.gantt.started_column)}' if card.gantt.started_column else 'NULL::timestamptz'
    params.update(gantt_search=context.gantt_search, gantt_offset=context.gantt_page * 50,
                  gantt_detail_lot=context.gantt_detail_lot)
    plan_columns = {c['name'] for c in sources['plan'].columns}
    main_col = "lower(p.main_oper_yn::text) IN ('y','true','1')" if 'main_oper_yn' in plan_columns else 'TRUE'
    # A skip flag belongs to the full plan, not to the editable display query.
    # SQL <> semantics intentionally also exclude NULL (use N for active steps).
    skip_filter = "p.skip_yn <> 'Y'" if 'skip_yn' in plan_columns else 'TRUE'
    project = managed_project(context.project_id)
    tat_scope = 'TRUE'
    if project:
        tat_scope = _filter_sql([Filter(column='lot_cd', op='in', value=project.lot_cds)], {'lot_cd'}, params, 'gantt_project')
    # Page WIP before reading history. Metadata table sizes never bound the
    # number of measurement rows sent to the browser: those tables are unused.
    # Materialize history at its complete lot/operation grain. Inlining its
    # aggregate can select a lot_cd-only merge and compare millions of pairs.
    sql = f'''{cte}, display_steps AS MATERIALIZED (
        SELECT DISTINCT lot_cd,lot_id,operation_seq,operation FROM filtered
    ), candidates AS MATERIALIZED (
        SELECT DISTINCT s.lot_cd,s.lot_id,s.operation AS current_operation,s.remark,{start_col} AS current_started_at
        FROM {tables['status']} s
        WHERE position(lower(:gantt_search) IN lower(s.lot_id))>0
          AND (:gantt_detail_lot='' OR s.lot_id=:gantt_detail_lot)
          AND EXISTS (SELECT 1 FROM display_steps d JOIN {tables['plan']} p
              USING(lot_cd,lot_id,operation_seq,operation)
              WHERE d.lot_cd=s.lot_cd AND d.lot_id=s.lot_id AND {skip_filter})
        ORDER BY s.lot_cd,s.lot_id LIMIT 51 OFFSET :gantt_offset
    ), picked AS (SELECT * FROM candidates ORDER BY lot_cd,lot_id LIMIT 50),
    route AS MATERIALIZED (SELECT p.lot_cd,p.lot_id,p.operation_seq,p.operation,{main_col} AS main_oper_yn,
        (d.operation_seq IS NOT NULL) AS displayed,s.current_operation,s.remark,s.current_started_at
        FROM {tables['plan']} p JOIN picked s USING(lot_cd,lot_id)
        LEFT JOIN display_steps d USING(lot_cd,lot_id,operation_seq,operation)
        WHERE {skip_filter}),
    hist AS MATERIALIZED (SELECT h.lot_cd,h.lot_id,h.operation,max(h.end_dt_tm) AS completed_at,count(*) AS history_count
        FROM {tables['history']} h JOIN picked s USING(lot_cd,lot_id) GROUP BY h.lot_cd,h.lot_id,h.operation),
    tat AS MATERIALIZED (SELECT t.lot_cd,t.operation,t.tat FROM {tables['tat']} t
        JOIN {tables['plan']} p USING(lot_cd,lot_id,operation)
        WHERE t.tat>0 AND {tat_scope} AND {skip_filter} AND t.operation IN (SELECT operation FROM route)),
    group_tat AS (SELECT lot_cd,operation,percentile_cont(.5) WITHIN GROUP(ORDER BY tat) AS expected_hours,count(*) AS samples
        FROM tat GROUP BY lot_cd,operation),
    all_tat AS (SELECT operation,percentile_cont(.5) WITHIN GROUP(ORDER BY tat) AS expected_hours,count(*) AS samples
        FROM tat GROUP BY operation)
    SELECT r.*,h.completed_at,coalesce(h.history_count,0) AS history_count,
        coalesce(g.expected_hours,a.expected_hours) AS expected_hours,coalesce(g.samples,a.samples,0) AS tat_samples,
        CASE WHEN g.samples IS NOT NULL THEN 'same_lot_cd' WHEN a.samples IS NOT NULL THEN 'all_lot_cd' ELSE 'unavailable' END AS tat_basis,
        (SELECT count(*)>50 FROM candidates) AS has_more
    FROM route r LEFT JOIN hist h USING(lot_cd,lot_id,operation)
    LEFT JOIN group_tat g USING(lot_cd,operation) LEFT JOIN all_tat a USING(operation)
    ORDER BY r.lot_cd,r.lot_id,r.operation_seq LIMIT 75001'''
    rows = execute_rows(sql, params)
    if len(rows) > 75000:
        raise HTTPException(422, '공정 구간이 너무 많습니다. 랏 필터를 좁혀 주세요.')
    as_of = as_time(card.gantt.as_of) or datetime.now(timezone.utc)
    segments = schedule_segments(rows, as_of)
    segments, summaries = display_segments(segments)
    # Only the drawing window follows the period; history must retain predecessors.
    period = context.period_start if card.period.use_dashboard else None
    visible_start = date.fromisocalendar(period.year, period.week, 1).isoformat() if period else None
    return clean({'rows': segments, 'meta': {'mode': 'aggregate', 'returned_rows': len(segments),
        'queried_at': datetime.now(timezone.utc), 'cached': False, 'ignored_filters': ignored,
        'as_of': as_of, 'snapshot': card.gantt.as_of is not None, 'tat_unit': 'hour',
        'dataset_version': compiled.get('dataset_version', card.dataset_version),
        'page': context.gantt_page, 'page_size': 50, 'has_more': bool(rows and rows[0]['has_more']),
        'lot_count': len(summaries), 'visible_start': visible_start,
        'lots': summaries, 'total_steps': len(rows), 'detail_lot': context.gantt_detail_lot,
        'skip_filter': "skip_yn <> 'Y'" if 'skip_yn' in plan_columns else None,
        'display_dataset_id': card.dataset_id}})


def compute_dataset_gantt(card, context, compiled):
    """Consume the authored JOIN result. No source table/column joins here."""
    from .query import _dataset_cte, _filter_sql, execute_rows, clean
    from .schemas import Filter
    extra = list(card.filters)
    if context.gantt_lot_cd:
        extra.append(Filter(column='lot_cd', op='eq', value=context.gantt_lot_cd))
    cte, params, ignored = _dataset_cte(compiled, context.model_copy(update={'filters': []}), extra)
    names = {c['name'] for c in compiled['columns']}
    lines = _filter_sql(card.gantt.line_filters, names, params, 'gantt_line')
    keys = _filter_sql(card.gantt.key_filters, names, params, 'gantt_key') if card.gantt.key_filters else 'FALSE'
    params.update(gantt_search=context.gantt_search, gantt_offset=context.gantt_page * 50,
                  gantt_detail_lot=context.gantt_detail_lot)
    # Conditions are boolean projections, never WHERE predicates on route steps.
    # This preserves every predecessor, hidden TAT and the hidden plan tail.
    sql = f'''{cte}, candidates AS MATERIALIZED (
        SELECT DISTINCT lot_cd, lot_id FROM filtered
        WHERE position(lower(:gantt_search) IN lower(lot_id))>0
          AND (:gantt_detail_lot='' OR lot_id=:gantt_detail_lot)
        ORDER BY lot_cd, lot_id LIMIT 51 OFFSET :gantt_offset
    ), picked AS (SELECT * FROM candidates ORDER BY lot_cd, lot_id LIMIT 50)
    SELECT f.*, coalesce(({lines}), FALSE) AS __gantt_line,
           coalesce(({keys}), FALSE) AS __gantt_key,
           (SELECT count(*)>50 FROM candidates) AS __gantt_more
    FROM filtered f JOIN picked USING (lot_cd, lot_id)
    ORDER BY lot_cd, lot_id, operation_seq LIMIT 75001'''
    rows = execute_rows(sql, params)
    if len(rows) > 75000:
        raise HTTPException(422, '공정 구간이 너무 많습니다. 랏 필터를 좁혀 주세요.')
    for row in rows:
        row['displayed'] = row.pop('__gantt_line')
        row['key_operation'] = row.pop('__gantt_key')
        if any(row[name] is None for name in ('lot_cd', 'lot_id', 'operation', 'operation_seq', 'history_count', 'tat_samples')):
            raise HTTPException(422, '일정 데이터셋의 랏·공정 키와 이력·TAT 표본 수에는 빈 값이 없어야 합니다.')
    as_of = as_time(card.gantt.as_of) or datetime.now(timezone.utc)
    segments, summaries = display_segments(schedule_segments(rows, as_of), presentation_filters=True)
    period = context.period_start if card.period.use_dashboard else None
    visible_start = date.fromisocalendar(period.year, period.week, 1).isoformat() if period else None
    return clean({'rows': segments, 'meta': {'mode': 'aggregate', 'returned_rows': len(segments),
        'queried_at': datetime.now(timezone.utc), 'cached': False, 'ignored_filters': ignored,
        'as_of': as_of, 'snapshot': card.gantt.as_of is not None, 'tat_unit': 'hour',
        'dataset_version': compiled.get('dataset_version', card.dataset_version),
        'page': context.gantt_page, 'page_size': 50, 'has_more': bool(rows and rows[0]['__gantt_more']),
        'lot_count': len(summaries), 'visible_start': visible_start, 'lots': summaries,
        'total_steps': len(rows), 'detail_lot': context.gantt_detail_lot,
        'schedule_dataset_id': card.dataset_id, 'data_mode': 'dataset'}})
