"""Exercise independent presentation filters and edited SQL against the demo DB."""
from copy import deepcopy
import json
import re
import sys
from types import SimpleNamespace
from time import perf_counter

from add_dashboard_examples import api, ROOT


def verify():
    assert api('/database-status')['database']=='wafer_fab_demo'
    board=api('/dashboards')
    card=next(c for c in board['cards'] if c['kind']=='gantt')
    original=api('/cards/query',dict(card=card,context={}))
    changed=deepcopy(card)
    changed['gantt']['line_filters']=[dict(column='operation',op='eq',value='no matching operation')]
    changed['gantt']['key_filters']=[dict(column='operation',op='eq',value='oper A CD')]
    flags=api('/cards/query',dict(card=changed,context={}))
    assert all(not r['displayed'] and r['key_operation'] for r in flags['rows'])
    assert len(flags['rows'])==18
    assert [s['ribbon'] for s in flags['meta']['lots']]==[s['ribbon'] for s in original['meta']['lots']]
    changed['gantt']['key_filters']=[]
    empty=api('/cards/query',dict(card=changed,context={}))
    assert all(not r['displayed'] and not r['key_operation'] for r in empty['rows'])
    assert [s['ribbon'] for s in empty['meta']['lots']]==[s['ribbon'] for s in original['meta']['lots']]
    changed['gantt']['line_filters']=[]
    all_lines=api('/cards/query',dict(card=changed,context={}))
    assert len(all_lines['rows'])==17100 and all(r['displayed'] for r in all_lines['rows'])
    # Exercise the chart execution path with an unsaved draft: no test dataset or
    # version is left behind. All physical reads use the configured read-only role.
    sys.path.insert(0,str(ROOT/'backend'))
    from app.database_settings import load_active_source
    from app.query import load_dataset, chart_query
    from app.schemas import CardSpec, QueryContext
    load_active_source()
    ds=load_dataset(card['dataset_id'])
    spec=deepcopy(ds.spec)
    sql=spec['raw_sql']
    spec['raw_sql'], count=re.subn(r'COALESCE\(g\.expected_hours,\s*a\.expected_hours\)',r'(\g<0> * 2)',sql)
    assert count==1
    draft=SimpleNamespace(id=ds.id,version=ds.version,project_id=ds.project_id,spec=spec,columns=ds.columns)
    started=perf_counter()
    result=chart_query(CardSpec.model_validate(card),QueryContext(),dataset=draft)
    for old,new in zip(original['rows'],result['rows']):
        assert (old['lot_id'],old['operation_seq'])==(new['lot_id'],new['operation_seq'])
        assert new['expected_hours']==old['expected_hours']*2
    assert original['meta']['lots'][0]['plan_end_at']!=result['meta']['lots'][0]['plan_end_at']
    assert load_dataset(card['dataset_id']).spec['raw_sql']==sql
    report=dict(lots=18,calculated_steps=17100,independent_flag_rows=len(flags['rows']),
                no_marker_ribbon_preserved=True,all_lines=len(all_lines['rows']),
                edited_sql_changes_chart=True,source_and_saved_sql_unchanged=True,
                draft_chart_seconds=round(perf_counter()-started,3))
    (ROOT/'.local/gantt-joined-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': verify()
