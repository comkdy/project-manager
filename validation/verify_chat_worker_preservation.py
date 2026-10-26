"""Compare all pre-existing metadata rows, allowing only append-only test effects."""
import json
import sys
from pathlib import Path
from sqlalchemy import text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import db as database

before=json.loads((ROOT/'validation/chat-worker-analysis-metadata-before.json').read_text())
append_only={'app_audit','app_semantic_contexts','app_dataset_versions','app_analysis_runs',
 'app_chat_conversations','app_chat_turns','app_semantic_questions','app_semantic_answers','app_semantic_collected_drafts'}
report={}
with database.engine.connect() as connection:
    connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    for name,expected in before.items():
        assert name.startswith('app_') and name.replace('_','').isalnum()
        query=f'SELECT * FROM public."{name}"'
        if name in append_only:
            order=('created_at, conversation_id, sequence' if name=='app_chat_turns' else
                   'created_at, id, version' if name=='app_dataset_versions' else 'created_at, id')
            query+=f' ORDER BY {order} LIMIT :count'
        count,value=connection.execute(text("SELECT count(*),md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM "
            f"(SELECT md5(to_jsonb(t)::text) h FROM ({query}) t) rows"),{'count':expected['rows']}).one()
        total=connection.scalar(text(f'SELECT count(*) FROM public."{name}"'))
        report[name]={'existing_rows':count,'unchanged':count==expected['rows'] and value==expected['digest'],'added':total-count}
    assert all(r['unchanged'] for r in report.values()),{k:v for k,v in report.items() if not v['unchanged']}
(ROOT/'validation/chat-worker-preservation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'tables':len(report),'existing_rows':sum(v['rows'] for v in before.values()),'unchanged':True,
    'additions':{k:v['added'] for k,v in report.items() if v['added']}}))
