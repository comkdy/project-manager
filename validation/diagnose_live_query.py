"""Read-only query plan inspection for the actual walkthrough dataset."""
import json,sys
from pathlib import Path
from sqlalchemy import text
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import db as database
from app.database_settings import load_active_source
from app.evaluation_analysis import _compile,Search
load_active_source()
s=json.loads((ROOT/'validation/live-case-state.json').read_text(encoding='utf-8'))
search=Search(query='inspect',dataset_id=s['dataset_apply']['dataset_id'],project_id=s['project_id'])
compiled,cte,params,period=_compile(search)
with database.current_source().engine.connect() as connection:
    connection.execute(text('SET TRANSACTION READ ONLY'))
    plan=connection.execute(text('EXPLAIN (FORMAT JSON) '+cte+' SELECT count(*) FROM filtered'),params).scalar()
(ROOT/'validation/live-query-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
def show(node,depth=0):
    print('  '*depth,node['Node Type'],'rows',node.get('Plan Rows'),'cost',node.get('Total Cost'),node.get('Join Type',''),node.get('Relation Name',''))
    for c in node.get('Plans',[]): show(c,depth+1)
show(plan[0]['Plan'])
