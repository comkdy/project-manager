import json
import sys
from pathlib import Path
from sqlalchemy import select
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import db as database
from app.database_settings import load_active_source
from app.source_discovery import database_tables
from app.semantic.collector_service import inventory
from app.analytics.models import AnalysisRun
from app.models import Source

load_active_source()
with database.SessionLocal() as db:
    sources=[{'id':s.id,'name':s.name,'columns':s.columns} for s in db.scalars(select(Source).where(
        Source.connection_key==database.source_database_key(),Source.table_name=='wafer_his'))]
    datasets=[d for d in inventory(db).values() if d['name']=='wafer_his' or d['id']=='aa3f3390-57a2-41b9-a185-a045dd3757c3']
    run=db.get(AnalysisRun,'4591b8bf-fb52-45a0-8626-0e1fd4bd176f')
    result={'physical_wafer_his':[t for t in database_tables() if t['table_name']=='wafer_his'],
        'sources':sources,'datasets':datasets,'approved_run_dataset':run.request['_evaluation_dataset']}
(ROOT/'validation/inline-items-before.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'physical_wafer_his':result['physical_wafer_his'],'sources':sources,
    'datasets':[{k:d[k] for k in ('id','name','version','project_id','columns','spec')} for d in datasets]},ensure_ascii=False))
