"""One-time SQL template generation; saved SQL is subsequently user owned."""
from sqlglot import exp, parse_one

from .expressions import quote
from .sql_display import generated_sql


def schedule_sql(sources, *, lot_cds=(), started_column=None):
    tables = {role: f'{quote(s.schema_name)}.{quote(s.table_name)}' for role, s in sources.items()}
    columns = {c['name'] for c in sources['plan'].columns}
    extras = ''.join(f', p.{quote(name)}' for name in ('area_id', 'samp_seq', 'main_oper_yn', 'skip_yn') if name in columns)
    skip = "p.skip_yn <> 'Y'" if 'skip_yn' in columns else 'TRUE'
    scope = 'TRUE'
    if lot_cds:
        values = ', '.join(exp.Literal.string(code).sql(dialect='postgres') for code in lot_cds)
        scope = f'p.lot_cd = {values}' if len(lot_cds) == 1 else f'p.lot_cd IN ({values})'
    started = f's.{quote(started_column)}' if started_column else 'NULL::timestamptz'
    sql = f'''WITH plan AS MATERIALIZED (
        SELECT p.lot_cd, p.lot_id, p.operation_seq, p.operation{extras}
        FROM {tables['plan']} p WHERE {skip} AND {scope}
    ), wip AS MATERIALIZED (
        SELECT s.lot_cd, s.lot_id, s.operation AS current_operation,
               s.remark, {started} AS current_started_at
        FROM {tables['status']} s
        WHERE EXISTS (SELECT 1 FROM plan p WHERE p.lot_cd=s.lot_cd AND p.lot_id=s.lot_id)
    ), route AS MATERIALIZED (
        SELECT p.*, s.current_operation, s.remark, s.current_started_at
        FROM plan p JOIN wip s USING (lot_cd, lot_id)
    ), hist AS MATERIALIZED (
        SELECT h.lot_cd, h.lot_id, h.operation,
               max(h.end_dt_tm) AS completed_at, count(*) AS history_count
        FROM {tables['history']} h
        WHERE EXISTS (SELECT 1 FROM wip s WHERE s.lot_cd=h.lot_cd AND s.lot_id=h.lot_id)
        GROUP BY h.lot_cd, h.lot_id, h.operation
    ), tat_samples AS MATERIALIZED (
        SELECT t.lot_cd, t.operation, t.tat
        FROM {tables['tat']} t JOIN plan p USING (lot_cd, lot_id, operation)
        WHERE t.tat > 0 AND t.operation IN (SELECT operation FROM route)
    ), group_tat AS (
        SELECT lot_cd, operation, percentile_cont(0.5) WITHIN GROUP (ORDER BY tat) AS expected_hours,
               count(*) AS samples FROM tat_samples GROUP BY lot_cd, operation
    ), all_tat AS (
        SELECT operation, percentile_cont(0.5) WITHIN GROUP (ORDER BY tat) AS expected_hours,
               count(*) AS samples FROM tat_samples GROUP BY operation
    )
    SELECT r.*, h.completed_at, coalesce(h.history_count, 0) AS history_count,
           coalesce(g.expected_hours, a.expected_hours) AS expected_hours,
           coalesce(g.samples, a.samples, 0) AS tat_samples,
           CASE WHEN g.samples IS NOT NULL THEN 'same_lot_cd'
                WHEN a.samples IS NOT NULL THEN 'all_lot_cd' ELSE 'unavailable' END AS tat_basis
    FROM route r LEFT JOIN hist h USING (lot_cd, lot_id, operation)
    LEFT JOIN group_tat g USING (lot_cd, operation) LEFT JOIN all_tat a USING (operation)'''
    return generated_sql(parse_one(sql, read='postgres'))
