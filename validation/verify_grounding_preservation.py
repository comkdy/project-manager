"""Check original metadata rows; allow only this verification's new chat/proposal/audit rows."""
import json
import sys
from pathlib import Path
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import db as database

before = json.loads((ROOT / 'validation/chat-grounding-metadata-before.json').read_text())
append_only = {'app_audit', 'app_semantic_contexts', 'app_chat_conversations', 'app_chat_turns', 'app_analysis_proposals'}
report = {}
with database.engine.connect() as connection:
    connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    for name, expected in before.items():
        assert name.startswith('app_') and name.replace('_', '').isalnum()
        order = 'created_at, conversation_id, sequence' if name == 'app_chat_turns' else 'created_at, id'
        query = f'SELECT * FROM public."{name}"'
        if name in append_only:
            query += f' ORDER BY {order} LIMIT :count'
        count, digest = connection.execute(text(
            "SELECT count(*),md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM "
            f"(SELECT md5(to_jsonb(t)::text) h FROM ({query}) t) rows"), {'count': expected['rows']}).one()
        assert count == expected['rows'] and digest == expected['digest'], f'Existing rows changed: {name}'
        total = connection.scalar(text(f'SELECT count(*) FROM public."{name}"'))
        report[name] = {'original_rows_unchanged': True, 'added_rows': total - count}
(ROOT / 'validation/chat-grounding-preservation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'tables': len(report), 'existing_rows_unchanged': True,
    'additions': {name: row['added_rows'] for name, row in report.items() if row['added_rows']}}, ensure_ascii=False))
