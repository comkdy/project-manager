"""Reconcile old rows while allowing only expected new audit/context/chat records."""
import json,sys
from pathlib import Path
from sqlalchemy import text
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import db as database
before=json.loads((ROOT/'validation/narrative-chat-metadata-before.json').read_text())
append_only={'app_audit','app_semantic_contexts','app_chat_conversations','app_chat_turns'}
report={}
with database.engine.connect() as conn:
    conn.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    for name,expected in before.items():
        # Names originate in the already-recorded pg_tables inventory.
        assert name.startswith('app_') and name.replace('_','').isalnum()
        order='created_at, id'
        if name=='app_chat_turns':order='created_at, conversation_id, sequence'
        sub=f'SELECT * FROM public."{name}"'
        if name in append_only:sub+=f' ORDER BY {order} LIMIT :count'
        count,digest=conn.execute(text(f"SELECT count(*),md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM (SELECT md5(to_jsonb(t)::text) h FROM ({sub}) t) rows"),{'count':expected['rows']}).one()
        assert count==expected['rows'] and digest==expected['digest'],f'Existing rows changed: {name}'
        total=conn.scalar(text(f'SELECT count(*) FROM public."{name}"'))
        report[name]={'original_rows_unchanged':True,'added_rows':total-count}
(ROOT/'validation/narrative-chat-preservation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'tables':len(report),'existing_rows_unchanged':True,'additions':{k:v['added_rows'] for k,v in report.items() if v['added_rows']}},ensure_ascii=False))
