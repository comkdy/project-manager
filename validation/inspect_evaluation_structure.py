"""Read-only audit of current evaluation records, input passages and data links."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import db as database
from app.database_settings import load_active_source
from app.semantic.models import KnowledgeItem, KnowledgeVersion
from app.semantic.collector_models import KnowledgeAnswer, CollectedDraft
from app.semantic.evaluations import evaluation_origin
from app.semantic.evaluation_context import public_case_context
from app.semantic.evaluation_terms import public_terms
from app.analytics.models import AnalysisRun
from app.measurement_items import context_for_run

load_active_source()
records = []
with database.SessionLocal() as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    items = list(db.scalars(select(KnowledgeItem).where(KnowledgeItem.kind=='evaluation',
        KnowledgeItem.connection_key==database.source_database_key()).order_by(KnowledgeItem.id)))
    for item in items:
        versions = list(db.scalars(select(KnowledgeVersion).where(KnowledgeVersion.item_id==item.id).order_by(KnowledgeVersion.version)))
        version = next((v for v in versions if v.version==item.published_version), versions[-1] if versions else None)
        if not version:
            continue
        origin = evaluation_origin(db, item.id, version.version)
        entry = {'id':item.id, 'published_version':item.published_version,
                 'version_status':version.status, 'content':version.content,
                 'stored_version_numbers':[v.version for v in versions]}
        if origin:
            answers = list(db.scalars(select(KnowledgeAnswer).where(KnowledgeAnswer.question_id==origin.id).order_by(KnowledgeAnswer.created_at)))
            drafts = list(db.scalars(select(CollectedDraft).where(CollectedDraft.question_id==origin.id).order_by(CollectedDraft.created_at)))
            entry.update(origin={'question_id':origin.id,'subject':origin.subject,'status':origin.status,
                                 'case_context':public_case_context(origin),'dataset':origin.dataset},
                         answers=[{'id':a.id,'text':a.text,'status':a.status} for a in answers],
                         drafts=[{'id':d.id,'status':d.status,'review_note':d.review_note,
                                  'evidence':d.evidence,'followups':d.followups} for d in drafts],
                         term_links=public_terms(db,version.content.get('details',{}).get('terms',[])))
            if origin.subject.startswith('evaluation-run:'):
                run = db.get(AnalysisRun,origin.subject.removeprefix('evaluation-run:'))
                if run:
                    entry['execution']={'id':run.id,'status':run.status,'settings':run.request.get('settings'),
                        'dataset':run.request.get('_evaluation_dataset'), 'scope':run.request.get('_evaluation_scope'),
                        'result':run.result, 'measurement_items':context_for_run(run.request)}
        records.append(entry)

report = {'inspected_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'raw_measurement_rows_read':False,'records':records}
target = ROOT / 'validation/evaluation-structure-current.json'
target.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'total_records':len(records),'records':[{
    'id':r['id'],'published_version':r['published_version'],'status':r['version_status'],
    'name':r['content']['name'],'description':r['content']['description'],
    'details':{k:v[:260] if isinstance(v,str) else v for k,v in r['content']['details'].items()},
    'origin_subject':r.get('origin',{}).get('subject'),'scope':r.get('origin',{}).get('case_context'),
    'answer_count':len(r.get('answers',[])),'term_statuses':[{k:t[k] for k in ('term','status','references')} for t in r.get('term_links',[])]
} for r in records]},ensure_ascii=False,indent=2))
