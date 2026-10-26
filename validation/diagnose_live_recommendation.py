"""Inspect an actual recommendation without changing application records."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import db as database
from app.analytics.models import AnalysisRun
from app import evaluation_recommendation as rec
from app.database_settings import load_active_source
load_active_source()

state=json.loads((ROOT/'validation/live-case-state.json').read_text(encoding='utf-8'))
with database.SessionLocal() as db:
    run=db.get(AnalysisRun,state['analysis_id'])
    facts,_=rec.facts_for(db,run)
    columns=run.request['_evaluation_columns']
    payload={'facts':facts,'columns':columns,'scope':{**run.request['_evaluation_scope'],'project_id':run.request.get('project_id')},'method_version':rec.VERSION}
plan=rec.llm.complete(rec.PROMPT,payload,rec.Plan)
result={'plan':plan.model_dump(mode='json')}
try:
    rec.validate_plan(plan,facts,columns)
    result['validation']='passed'
except Exception as exc:
    result['validation']=str(exc)
    by_ref={f['reference']:f for f in facts}
    result['citations']=[{'reference':c.reference,'quote':c.quote,'valid':c.reference in by_ref and c.quote in by_ref[c.reference]['text']} for c in [plan.row_basis,*[v.evidence for v in plan.columns]] if c]
(ROOT/'validation/live-recommendation-diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
