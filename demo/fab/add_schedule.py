"""Add lot schedule fixtures only to the identified local wafer_fab_demo DB.

No overwrite/drop option; all DB changes commit together. Existing fixture rows
are fingerprinted before/after. The app continues using its read-only account.
"""
from datetime import datetime
import json
from pathlib import Path
import sys

from psycopg import sql

from generator import COLUMNS, Config, GROUPS, TABLES
from schedule_generator import SCHEDULE_COLUMNS, plan, wip_lots
from seed import DATABASE, MARKER, ROOT, api, connect, runtime

EXTENSION_MARKER = 'project-manager-fab-schedule-v1'
REPORT_PATH = ROOT / '.local/fab-schedule-manifest.json'


def fingerprint(connection, table, excluded_lots):
    # Order-independent full-row hash + count; no measured values leave the DB.
    return connection.execute(sql.SQL('''SELECT count(*), coalesce(sum(hashtextextended(row_to_json(t)::text, 0)::numeric),0)::text
        FROM public.{} t WHERE NOT (lot_id = ANY(%s))''').format(sql.Identifier(table)), (excluded_lots,)).fetchone()


def copy_rows(connection, table, columns, rows):
    if rows:
        with connection.cursor().copy(sql.SQL('COPY public.{} ({}) FROM STDIN').format(
                sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)))) as copy:
            for row in rows:
                copy.write_row(tuple(row[column] for column in columns))


def create():
    manifest = json.loads((ROOT / '.local/fab-manifest.json').read_text(encoding='utf-8'))
    assert manifest['marker'] == MARKER and manifest['database'] == DATABASE
    config = Config(manifest['seed'], manifest['lots_per_group'], datetime.fromisoformat(manifest['anchor']))
    before_app = {'bootstrap': api('/bootstrap'), 'dashboard': api('/dashboards')}
    assert api('/database-status')['database'] == DATABASE, 'Activate wafer_fab_demo before discovery verification'
    baseline = {}
    extra_counts = dict.fromkeys(TABLES, 0)
    schedule_counts = dict.fromkeys(SCHEDULE_COLUMNS, 0)
    provenance = []
    with connect(runtime()) as connection:
        connection.execute("SET LOCAL lock_timeout='5s'")
        connection.execute("SET LOCAL statement_timeout='60s'")
        connection.execute('SELECT pg_advisory_xact_lock(2026102209)')
        identity = connection.execute("SELECT shobj_description(oid,'pg_database'), pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        assert identity == (MARKER, 'wafer_local_admin'), 'Unrecognized fixture database'
        for table in SCHEDULE_COLUMNS:
            assert connection.execute('SELECT to_regclass(%s)', (f'public.{table}',)).fetchone()[0] is None, 'Schedule already exists; use verify_schedule.py'
        for table in TABLES:
            baseline[table] = fingerprint(connection, table, [])
            assert baseline[table][0] == manifest['counts'][table], 'Base fixture was changed; inspect it before adding schedules'
        baseline_history = connection.execute('SELECT lot_cd,lot_id,operation,end_dt_tm FROM lot_his ORDER BY lot_cd,lot_id,end_dt_tm,record_id').fetchall()
        existing_lots = sorted({(r[0], r[1]) for r in baseline_history})
        connection.execute((Path(__file__).parent / 'schedule_schema.sql').read_text(encoding='utf-8'))
        for table in SCHEDULE_COLUMNS:
            for group in GROUPS:
                connection.execute(sql.SQL('CREATE TABLE public.{} PARTITION OF public.{} FOR VALUES IN ({})').format(
                    sql.Identifier(f'{table}_{group.lower()}'), sql.Identifier(table), sql.Literal(group)))
        original_plans = [row for group, lot_id in existing_lots for row in plan(group, lot_id)]
        copy_rows(connection, 'lot_plan', SCHEDULE_COLUMNS['lot_plan'], original_plans)
        schedule_counts['lot_plan'] += len(original_plans)
        previous = {}
        historical_tat = []
        for group, lot_id, operation, end in baseline_history:
            key = group, lot_id
            if key in previous:
                hours = round((end - previous[key]).total_seconds() / 3600, 3)
                historical_tat.append(dict(lot_cd=group, lot_id=lot_id, operation=operation, tat=hours))
            previous[key] = end
        copy_rows(connection, 'operation_tat', SCHEDULE_COLUMNS['operation_tat'], historical_tat)
        schedule_counts['operation_tat'] += len(historical_tat)
        for rows, schedule, origin in wip_lots(config):
            assert not any(lot_id == origin['lot_id'] for _, lot_id in existing_lots), 'WIP lot name collision'
            provenance.append(origin)
            for table in TABLES:
                copy_rows(connection, table, COLUMNS[table], rows[table])
                extra_counts[table] += len(rows[table])
            for table in SCHEDULE_COLUMNS:
                copy_rows(connection, table, SCHEDULE_COLUMNS[table], schedule[table])
                schedule_counts[table] += len(schedule[table])
        wip_ids = [row['lot_id'] for row in provenance]
        assert all(fingerprint(connection, table, wip_ids) == baseline[table] for table in TABLES), 'Existing rows changed'
        connection.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO wafer_source')
        # Persist manifest in the table comment atomically with the fixture.
        report = dict(marker=EXTENSION_MARKER, database=DATABASE, anchor=config.anchor.isoformat(),
                      tat_unit='hour', tat_semantics='elapsed including waiting',
                      counts=schedule_counts, added_history_counts=extra_counts,
                      original_fingerprints=baseline, original_lots=len(existing_lots),
                      wip_lots=provenance, existing_rows_preserved=True)
        connection.execute(sql.SQL('COMMENT ON TABLE public.current_status IS {}').format(
            sql.Literal('합성 테스트 WIP snapshot; ' + json.dumps(report, ensure_ascii=False))))
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / '.local/fab-schedule-before-app.json').write_text(json.dumps(before_app, ensure_ascii=False, indent=2), encoding='utf-8')
    with connect(runtime(), autocommit=True) as connection:
        connection.execute('ANALYZE')
    print(json.dumps({k: report[k] for k in ('database', 'counts', 'added_history_counts', 'existing_rows_preserved')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        create()
    except Exception as error:
        print(f'Schedule fixture failed ({type(error).__name__}); no existing data is overwritten.', file=sys.stderr)
        if isinstance(error, AssertionError):
            print(str(error), file=sys.stderr)
        sys.exit(1)
