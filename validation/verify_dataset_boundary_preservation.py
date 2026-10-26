"""Verify baseline metadata rows, allowing only known append-only verification effects."""
import json
import sys
from pathlib import Path
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import db as database

before = json.loads((ROOT / 'validation/dataset-boundaries-metadata-before.json').read_text())
append_only = {'app_audit', 'app_semantic_contexts', 'app_dataset_versions'}
report = {}
with database.engine.connect() as connection:
    connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    for name, expected in before.items():
        assert name.startswith('app_') and name.replace('_', '').isalnum()
        order = 'created_at, id, version' if name == 'app_dataset_versions' else 'created_at, id'
        query = f'SELECT * FROM public."{name}"'
        if name in append_only:
            query += f' ORDER BY {order} LIMIT :count'
        count, digest = connection.execute(text(
            "SELECT count(*),md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM "
            f"(SELECT md5(to_jsonb(t)::text) h FROM ({query}) t) rows"), {'count': expected['rows']}).one()
        assert count == expected['rows'] and digest == expected['digest'], f'Existing rows changed: {name}'
        total = connection.scalar(text(f'SELECT count(*) FROM public."{name}"'))
        report[name] = {'original_rows_unchanged': True, 'added_rows': total - count}
    # New source definitions are defaults for the legacy demo projects exercised by tests.
    new_definitions = connection.execute(text(
        'SELECT project_id, count(*) FROM (SELECT project_id FROM app_dataset_versions '
        'ORDER BY created_at, id, version OFFSET :count) t GROUP BY project_id'),
        {'count': before['app_dataset_versions']['rows']}).all()
    assert all(project_id in {1, 2} for project_id, _ in new_definitions), new_definitions
(ROOT / 'validation/dataset-boundaries-preservation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'tables': len(report), 'existing_rows': sum(v['rows'] for v in before.values()),
    'existing_rows_unchanged': True,
    'additions': {name: row['added_rows'] for name, row in report.items() if row['added_rows']},
    'new_default_dataset_scopes': dict(new_definitions)}, ensure_ascii=False))
