"""Verify source equipment, exact project scopes, and preserved app records."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'demo/fab'))
from sqlalchemy import select
from app import db as database
from app.database_settings import load_active_source
from app.models import DatasetVersion
from app.semantic.collector_service import inventory
from approve_chat_worker_result import snapshot
from generator import equipment_id
from seed import connect, runtime

load_active_source()
with connect(runtime(), reader=True) as conn:
    identities = conn.execute('SELECT lot_id,wf_id,operation,eqp_id FROM public.wafer_his').fetchall()
    assert identities and all(eqp == equipment_id(lot, wafer, operation) for lot,wafer,operation,eqp in identities)
    assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name LIKE 'wafer_his%' AND column_name='eqp_id' AND is_nullable='NO'").fetchone()[0] == 4
with database.SessionLocal() as db:
    datasets = [d for d in inventory(db).values() if d['name']=='wafer_his']
    assert {d['project_id'] for d in datasets} == {None,1000004,1000005,1000006}
    for d in datasets:
        assert 'eqp_id' in {c['name'] for c in d['columns']} and 'eqp_id' in d['spec']['raw_sql']
        if d['project_id']:
            assert {1000004:'XXXA',1000005:'XXXB',1000006:'XXXC'}[d['project_id']] in d['spec']['raw_sql']
    joined = db.scalars(select(DatasetVersion).where(DatasetVersion.id=='aa3f3390-57a2-41b9-a185-a045dd3757c3').order_by(DatasetVersion.version.desc())).first()
    assert joined.version == 2 and 'eqp_id' not in {c['name'] for c in joined.columns}
before = json.loads((ROOT / 'validation/wafer-equipment-metadata-before.json').read_text(encoding='utf-8'))
after = snapshot()
changes = {table:{'changed':[key for key,h in rows.items() if key in after[table] and after[table][key]!=h],
                  'removed':list(rows.keys()-after[table].keys()),'added':list(after[table].keys()-rows.keys())}
           for table,rows in before.items()}
for table,delta in changes.items():
    assert not delta['removed'], (table,delta)
    if delta['changed']:
        assert table=='app_sources' and delta['changed']==['["auto_e9e22fc931d919c164af23a53b1f8f0d1ab013d2f918d4b6"]'], (table,delta)
    if table=='app_dataset_versions':
        assert len(delta['added'])==4, delta
    elif table not in {'app_audit','app_chat_conversations','app_chat_turns','app_semantic_contexts'}:
        assert not delta['added'], (table,delta)
report={'source_rows_verified':len(identities),'datasets':[
    {k:d[k] for k in ('id','version','name','project_id','columns','spec')} for d in datasets],
    'original_rows_verified':sum(len(v) for v in before.values()),
    'original_dataset_card_knowledge_and_chat_values_preserved':True,
    'changes':{k:v for k,v in changes.items() if any(v.values())}}
(ROOT / 'validation/wafer-equipment-preservation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='datasets'},ensure_ascii=False))
