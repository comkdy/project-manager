"""Read-only live checks: scope lists, raw SQL, aggregate input and existing cards."""
import json
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def call(path,body=None):
    request=urllib.request.Request('http://127.0.0.1:8000/api'+path,
        data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:
        return json.load(response)


common=call('/bootstrap')
report={'projects':{},'queries':{},'cards':{}}
for project in common['projects']:
    current=call('/bootstrap?project_id='+str(project['id']))
    assert all(d['project_id']==project['id'] for d in current['datasets'])
    assert not {d['id'] for d in current['datasets']} & {d['id'] for d in common['datasets']}
    assert all(d['kind'] in {'source','derived'} for d in current['datasets'])
    report['projects'][project['name']]={k:sum(d['kind']==k for d in current['datasets']) for k in ('source','derived')}
    if project['name']!='XXXA':
        continue
    source=next(s for s in current['catalog'] if s['table_name']=='inline_data')
    queries={
        'missing_filter':'SELECT lot_cd, COUNT(*) AS n FROM public.inline_data GROUP BY lot_cd',
        'override_attempt':"SELECT lot_cd, COUNT(*) AS n FROM public.inline_data WHERE lot_cd='XXXB' OR TRUE GROUP BY lot_cd",
        'nested_union':'WITH x AS (SELECT * FROM public.inline_data) SELECT lot_cd FROM x GROUP BY lot_cd UNION SELECT lot_cd FROM public.epm_data GROUP BY lot_cd',
        'join':'SELECT i.lot_cd, e.lot_cd AS epm_lot_cd FROM public.inline_data i JOIN public.epm_data e ON i.lot_id=e.lot_id AND i.wf_id=e.wf_id GROUP BY i.lot_cd,e.lot_cd',
    }
    def preview(sql,pid=project['id']):
        return call('/datasets/preview',{'spec':{'source_id':source['id'],'raw_sql':sql},'context':{'project_id':pid}})
    for name,sql in queries.items():
        value=preview(sql)
        assert value['rows'] and all(row['lot_cd']=='XXXA' and row.get('epm_lot_cd','XXXA')=='XXXA' for row in value['rows'])
        assert value['spec']['raw_sql']==sql
        report['queries'][name]=value['rows']
    aggregate=preview('SELECT COUNT(*) AS n FROM public.inline_data')['rows']
    expected=preview("SELECT COUNT(*) AS n FROM public.inline_data WHERE lot_cd='XXXA'",None)['rows']
    assert aggregate==expected
    report['queries']['aggregation_without_scope_column']=aggregate
    for dataset in current['datasets']:
        value=call('/query',{'dataset_id':dataset['id'],'dataset_version':dataset['version'],'context':{'project_id':project['id'],'limit':3}})
        assert all(row.get('lot_cd','XXXA')=='XXXA' for row in value['rows']),dataset['name']
    board=call('/dashboards?project_id='+str(project['id']))
    for card in board['cards']:
        result=call('/cards/query',{'card':card,'context':{'project_id':project['id'],'period_start':{'year':2026,'week':40},'refresh':True}})
        report['cards'][card['title']]={'ok':True,'rows':result['meta']['returned_rows']}
report['common_preserved']={'source':sum(d['kind']=='source' for d in common['datasets']),
                            'derived':sum(d['kind']=='derived' for d in common['datasets'])}
(ROOT/'validation/dataset-boundaries-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
