"""Read-only DB and app checks for the additive schedule fixture."""
from datetime import datetime
import json

from add_schedule import EXTENSION_MARKER, REPORT_PATH, fingerprint
from generator import GROUPS, TABLES
from schedule_generator import SCHEDULE_COLUMNS
from seed import DATABASE, MARKER, ROOT, api, connect, runtime


def verify():
    manifest = json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    assert manifest['marker'] == EXTENSION_MARKER and manifest['database'] == DATABASE
    assert manifest['tat_unit'] == 'hour'
    wip_ids = [r['lot_id'] for r in manifest['wip_lots']]
    report = dict(database=DATABASE, tat_unit='hour', counts={}, partitions={}, checks={})
    with connect(runtime(), reader=True) as connection:
        assert connection.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
        assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0] == MARKER

        def check(name, query, params=None):
            assert connection.execute(query, params).fetchone()[0] == 0, name
            report['checks'][name] = True

        for table in TABLES:
            assert list(fingerprint(connection, table, wip_ids)) == manifest['original_fingerprints'][table], table
        report['checks']['original_six_tables_preserved'] = True
        for table in SCHEDULE_COLUMNS:
            report['counts'][table] = connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            assert report['counts'][table] == manifest['counts'][table]
            assert connection.execute('SELECT pg_get_partkeydef(%s::regclass)', (table,)).fetchone()[0] == 'LIST (lot_cd)'
            report['partitions'][table] = connection.execute('SELECT count(*) FROM pg_inherits WHERE inhparent=%s::regclass', (table,)).fetchone()[0]
            assert report['partitions'][table] == len(GROUPS)
            assert connection.execute('SELECT has_table_privilege(current_user,%s,\'SELECT\')', (table,)).fetchone()[0]
            for permission in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
                assert not connection.execute('SELECT has_table_privilege(current_user,%s,%s)', (table, permission)).fetchone()[0]
        report['checks']['source_role_read_only'] = True
        check('plan_has_nine_contiguous_steps', '''SELECT count(*) FROM (
            SELECT lot_cd,lot_id,count(*) n,min(operation_seq) lo,max(operation_seq) hi
            FROM lot_plan GROUP BY lot_cd,lot_id) q WHERE n<>9 OR lo<>1 OR hi<>9''')
        check('current_step_and_future_have_no_completion', '''SELECT count(*) FROM current_status c
            JOIN lot_plan cp USING(lot_cd,lot_id,operation)
            JOIN lot_plan p ON p.lot_cd=c.lot_cd AND p.lot_id=c.lot_id AND p.operation_seq>=cp.operation_seq
            JOIN lot_his h ON h.lot_cd=p.lot_cd AND h.lot_id=p.lot_id AND h.operation=p.operation''')
        check('completed_prefix_is_contiguous', '''SELECT count(*) FROM current_status c
            JOIN lot_plan cp USING(lot_cd,lot_id,operation)
            JOIN lot_plan p ON p.lot_cd=c.lot_cd AND p.lot_id=c.lot_id AND p.operation_seq<cp.operation_seq
            WHERE NOT EXISTS (SELECT 1 FROM lot_his h WHERE h.lot_cd=p.lot_cd AND h.lot_id=p.lot_id AND h.operation=p.operation)''')
        check('tat_has_only_completed_operations', '''SELECT count(*) FROM operation_tat t WHERE NOT EXISTS (
            SELECT 1 FROM lot_his h WHERE h.lot_cd=t.lot_cd AND h.lot_id=t.lot_id AND h.operation=t.operation)''')
        check('tat_matches_elapsed_hours', '''WITH h AS (
            SELECT lot_cd,lot_id,operation,end_dt_tm,
            lag(end_dt_tm) OVER (PARTITION BY lot_cd,lot_id ORDER BY end_dt_tm,record_id) AS previous_end
            FROM lot_his)
            SELECT count(*) FROM h LEFT JOIN operation_tat t USING(lot_cd,lot_id,operation)
            WHERE previous_end IS NOT NULL AND (tat IS NULL OR tat<>round(extract(epoch FROM (end_dt_tm-previous_end))/3600,3))''')
        check('unknown_original_first_tat_is_absent', '''SELECT count(*) FROM operation_tat
            WHERE operation='oper A' AND NOT (lot_id=ANY(%s))''', (wip_ids,))
        for origin in manifest['wip_lots']:
            row = connection.execute('''SELECT h.end_dt_tm,t.tat FROM lot_his h
                JOIN operation_tat t USING(lot_cd,lot_id,operation)
                WHERE h.lot_cd=%s AND h.lot_id=%s AND h.operation='oper A' ''', (origin['lot_cd'], origin['lot_id'])).fetchone()
            assert float(row[1]) == round((row[0] - datetime.fromisoformat(origin['started_at'])).total_seconds()/3600, 3)
        report['checks']['first_wip_tat_matches_known_fixture_start'] = True
        check('no_future_history', 'SELECT count(*) FROM lot_his WHERE end_dt_tm>=%s', (manifest['anchor'],))
        assert connection.execute('SELECT count(DISTINCT operation) FROM current_status').fetchone()[0] == 8
        assert connection.execute('SELECT count(*) FROM (SELECT DISTINCT lot_cd,lot_id FROM lot_plan) q').fetchone()[0] == 78
    before = json.loads((ROOT / '.local/fab-schedule-before-app.json').read_text(encoding='utf-8'))
    assert api('/database-status')['database'] == DATABASE
    bootstrap = api('/bootstrap')  # Discover the three new parents, excluding partitions.
    datasets = {d['id']: d for d in bootstrap['datasets']}
    for original in before['bootstrap']['datasets']:
        assert datasets[original['id']] == original, original['name']
    assert len(datasets) == len(before['bootstrap']['datasets']) + 3
    assert api('/dashboards') == before['dashboard']
    report['checks']['existing_datasets_and_dashboard_preserved'] = True
    report['preview_rows'] = {}
    for table, columns in SCHEDULE_COLUMNS.items():
        dataset, = [d for d in datasets.values() if d['name'] == table]
        preview = api('/query', {'dataset_id': dataset['id'], 'dataset_version': dataset['version'], 'context': {'limit': 100}})
        names = [c['name'] if isinstance(c, dict) else c for c in preview['columns']]
        assert names == list(columns), (table, names)
        report['preview_rows'][table] = len(preview['rows'])
        assert 0 < len(preview['rows']) <= 100
        if table == 'operation_tat':
            with connect(runtime(), reader=True) as connection:
                stored = {(lot_cd, lot_id, operation): float(tat) for lot_cd, lot_id, operation, tat in
                          connection.execute('SELECT lot_cd,lot_id,operation,tat FROM operation_tat')}
            assert all(float(row['tat']) == stored[row['lot_cd'], row['lot_id'], row['operation']]
                       for row in preview['rows'])
            report['checks']['app_preview_tat_values_match_hours'] = True
    example_sql = (ROOT / 'demo/fab/schedule_examples.sql').read_text(encoding='utf-8')
    with connect(runtime(), reader=True) as connection:
        example_rows = connection.execute(example_sql).fetchall()
        assert len(example_rows) == 162
        assert sum(row[4] == 'current' for row in example_rows) == 18
        assert all((row[5] is not None) == (row[4] == 'completed') for row in example_rows)
        assert all(row[6] > 0 and row[7] > 0 for row in example_rows)
    source, = [s for s in bootstrap['catalog'] if s['table_name'] == 'lot_plan']
    example = api('/datasets/preview', {'spec': {'source_id': source['id'], 'raw_sql': example_sql},
                                      'editor_mode': 'sql', 'context': {'limit': 100}})
    assert len(example['rows']) == 100
    report['checks']['gantt_input_example_db_and_app_preview'] = True
    report['gantt_input_rows'] = len(example_rows)
    report['logical_tables'] = sorted(d['name'] for d in datasets.values())
    (ROOT / '.local/fab-schedule-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    verify()
