"""Read-only integrity and query checks for the identified synthetic fab DB."""
from datetime import datetime, timedelta
import json

from generator import GROUPS, TABLES
from seed import DATABASE, MARKER, ROOT, connect, runtime


def verify():
    manifest = json.loads((ROOT / '.local/fab-manifest.json').read_text(encoding='utf-8'))
    assert manifest['marker'] == MARKER and manifest['database'] == DATABASE
    extension_path = ROOT / '.local/fab-schedule-manifest.json'
    extension = json.loads(extension_path.read_text(encoding='utf-8')) if extension_path.exists() else {}
    report = {'database': DATABASE, 'checks': {}, 'counts': {}, 'partitions': {}}
    with connect(runtime(), reader=True) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        assert connection.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
        assert connection.execute("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0] == MARKER

        def scalar(statement, params=None):
            return connection.execute(statement, params).fetchone()[0]

        def check(name, statement, params=None):
            invalid = scalar(statement, params)
            assert invalid == 0, (name, invalid)
            report['checks'][name] = True

        for table in TABLES:  # Fixed identifiers from the generator contract.
            report['counts'][table] = scalar(f'SELECT count(*) FROM public.{table}')
            assert report['counts'][table] == manifest['counts'][table] + extension.get('added_history_counts', {}).get(table, 0), table
            assert scalar('SELECT pg_get_partkeydef(%s::regclass)', (f'public.{table}',)) == 'LIST (lot_cd)'
            children = connection.execute('''SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
                FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid
                WHERE i.inhparent=%s::regclass ORDER BY c.relname''', (f'public.{table}',)).fetchall()
            assert len(children) == len(GROUPS)
            report['partitions'][table] = [name for name, _ in children]
            check(table + '_lot_prefix', f'SELECT count(*) FROM public.{table} WHERE lot_cd<>left(lot_id,4)')
            check(table + '_unique_record', f'SELECT count(*) FROM (SELECT record_id FROM public.{table} GROUP BY record_id HAVING count(*)>1) q')
        check('wafer_count_10_to_15', '''SELECT count(*) FROM (
            SELECT lot_cd,lot_id,count(DISTINCT wf_id) n,min(wf_id) lo,max(wf_id) hi
            FROM wafer_his GROUP BY lot_cd,lot_id) q WHERE n NOT BETWEEN 10 AND 15 OR lo<>1 OR hi<>n''')
        report['lots'] = scalar('SELECT count(*) FROM (SELECT DISTINCT lot_cd,lot_id FROM lot_his) q')
        report['wafers'] = scalar('SELECT count(*) FROM (SELECT DISTINCT lot_cd,lot_id,wf_id FROM wafer_his) q')
        assert report['lots'] == manifest['lot_count'] + len(extension.get('wip_lots', []))
        for table, operation in [('inline_data', 'm.operation'), ('epm_data', "'EPM'")]:
            check(table + '_history', f'''SELECT count(*) FROM {table} m WHERE NOT EXISTS (
                SELECT 1 FROM wafer_his w WHERE w.lot_cd=m.lot_cd AND w.lot_id=m.lot_id AND w.wf_id=m.wf_id
                AND w.operation={operation} AND w.end_dt_tm<=m.end_dt_tm)''')
            check(table + '_before_rejection', f'''SELECT count(*) FROM {table} m JOIN reject_his r
                USING (lot_cd,lot_id,wf_id) WHERE m.end_dt_tm>=r.end_dt_tm''')
        check('wafer_has_lot_event', '''SELECT count(*) FROM wafer_his w WHERE NOT EXISTS (
            SELECT 1 FROM lot_his l WHERE l.lot_cd=w.lot_cd AND l.lot_id=w.lot_id
            AND l.operation=w.operation AND l.end_dt_tm<=w.end_dt_tm)''')
        check('no_process_after_rejection', '''SELECT count(*) FROM wafer_his w JOIN reject_his r
            USING (lot_cd,lot_id,wf_id) WHERE w.end_dt_tm>=r.end_dt_tm''')
        check('rejected_wafer_exists', '''SELECT count(*) FROM reject_his r WHERE NOT EXISTS (
            SELECT 1 FROM wafer_his w WHERE w.lot_cd=r.lot_cd AND w.lot_id=r.lot_id AND w.wf_id=r.wf_id)''')
        check('tem_links_to_rejected_wafer', '''SELECT count(*) FROM tem_his t LEFT JOIN reject_his r
            ON r.lot_cd=t.lot_cd AND r.record_id=t.reject_id AND r.lot_id=t.lot_id AND r.wf_id=t.wf_id
            WHERE r.record_id IS NULL OR r.reject_reason<>'TEM 시편 제작' OR t.end_dt_tm<=r.end_dt_tm''')
        report['tem_pending_wafers'] = scalar('''SELECT count(*) FROM reject_his r WHERE reject_reason='TEM 시편 제작'
            AND NOT EXISTS (SELECT 1 FROM tem_his t WHERE t.lot_cd=r.lot_cd AND t.reject_id=r.record_id)''')
        report['same_time_remeasure_groups'] = scalar('''SELECT count(*) FROM (SELECT lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter,end_dt_tm
            FROM inline_data GROUP BY lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter,end_dt_tm HAVING count(*)>1) q''')
        report['latest_null_groups'] = scalar('''SELECT count(*) FROM (
            SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter) value FROM inline_data
            ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter,end_dt_tm DESC,record_id DESC) q WHERE value IS NULL''')
        assert report['same_time_remeasure_groups'] > 0 and report['latest_null_groups'] > 0
        anchor = datetime.fromisoformat(manifest['anchor'])
        recent_start = anchor - timedelta(days=anchor.weekday() + 21)
        report['recent_four_weeks_from'] = recent_start.isoformat()
        report['recent_inline_rows'] = scalar('SELECT count(*) FROM inline_data WHERE end_dt_tm>=%s', (recent_start,))
        assert 0 < report['recent_inline_rows'] < report['counts']['inline_data']
        check('no_future_measurements', 'SELECT count(*) FROM inline_data WHERE end_dt_tm>=%s', (anchor,))
        # Prove partition pruning uses only the selected lot group.
        plan = connection.execute("EXPLAIN (FORMAT JSON) SELECT * FROM inline_data WHERE lot_cd='XXXA' AND lot_id='XXXA1' LIMIT 100").fetchone()[0]
        relations = set()

        def walk(node):
            if isinstance(node, dict):
                if 'Relation Name' in node:
                    relations.add(node['Relation Name'])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(plan)
        assert relations == {'inline_data_xxxa'}, relations
        report['partition_pruning'] = sorted(relations)
        report['source_read_only'] = True
    (ROOT / '.local/fab-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


if __name__ == '__main__':
    verify()
