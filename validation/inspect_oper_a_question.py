"""Read-only exploratory answer to the user's actual oper A question."""
import json
from pathlib import Path
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]

def call(path, body=None):
    req = urllib.request.Request('http://127.0.0.1:8000/api'+path,
        data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode()) from None

bootstrap=call('/bootstrap')
source=next(d for d in bootstrap['datasets'] if d['name']=='inline_data')['spec']['source_id']
project=next(p for p in bootstrap['projects'] if p['code']=='XXXA')

def query(sql, limit=20000):
    result=call('/datasets/preview',{'spec':{'source_id':source,'raw_sql':sql},
        'context':{'project_id':project['id'],'limit':limit}})
    assert len(result['rows'])<min(limit,100), 'Do not analyze a truncated preview'
    return result

catalog=query("""SELECT 'history' AS family,operation,recipe AS parameter,count(*) AS rows,
min(end_dt_tm) AS first_at,max(end_dt_tm) AS last_at FROM public.wafer_his
WHERE lot_cd='XXXA' AND replace(lower(operation),' ','')='opera' GROUP BY operation,recipe
UNION ALL SELECT 'inline',operation,parameter,count(*),min(end_dt_tm),max(end_dt_tm)
FROM public.inline_data WHERE lot_cd='XXXA' AND operation='oper A CD' GROUP BY operation,parameter
UNION ALL SELECT 'epm','',parameter,count(*),min(end_dt_tm),max(end_dt_tm)
FROM public.epm_data WHERE lot_cd='XXXA' GROUP BY parameter ORDER BY family,operation,parameter""")
assert {r['operation'] for r in catalog['rows'] if r['family']=='history'}=={'oper A'}

SQL="""WITH hist AS MATERIALIZED (
 SELECT *,lead(end_dt_tm) OVER (PARTITION BY lot_cd,lot_id,wf_id ORDER BY end_dt_tm,record_id) AS next_at,
 count(*) OVER (PARTITION BY lot_cd,lot_id,wf_id) AS visits
 FROM public.wafer_his WHERE lot_cd='XXXA' AND operation='oper A'
), measurements AS MATERIALIZED (
 SELECT 'inline' AS family,lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter,value,end_dt_tm,record_id
 FROM public.inline_data WHERE lot_cd='XXXA' AND operation='oper A CD'
 AND end_dt_tm >= TIMESTAMPTZ '2026-09-28T00:00:00+09:00' AND end_dt_tm<=CURRENT_TIMESTAMP
 UNION ALL
 SELECT 'epm',lot_cd,lot_id,wf_id,die_x,die_y,'' AS operation,parameter,value,end_dt_tm,record_id
 FROM public.epm_data WHERE lot_cd='XXXA'
 AND end_dt_tm >= TIMESTAMPTZ '2026-09-28T00:00:00+09:00' AND end_dt_tm<=CURRENT_TIMESTAMP
), latest AS MATERIALIZED (
 SELECT DISTINCT ON (family,lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter) * FROM measurements
 ORDER BY family,lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter,end_dt_tm DESC,record_id DESC
), matched AS (
 SELECT m.*,h.recipe,h.visits,h.end_dt_tm AS process_at
 FROM latest m LEFT JOIN hist h ON (m.lot_cd,m.lot_id,m.wf_id)=(h.lot_cd,h.lot_id,h.wf_id)
 AND h.end_dt_tm<=m.end_dt_tm AND (h.next_at IS NULL OR m.end_dt_tm<h.next_at)
)
SELECT family,operation,parameter,lot_id,wf_id,
 min(recipe) AS recipe,count(DISTINCT recipe) AS recipes,max(visits) AS visits,
 count(*) AS sites,count(value) AS valid_sites,count(*) FILTER (WHERE recipe IS NULL) AS unmatched_sites,
 avg(value) AS value,min(end_dt_tm) AS first_at,max(end_dt_tm) AS measured_at,
 max(process_at) AS process_at FROM matched
GROUP BY family,operation,parameter,lot_id,wf_id ORDER BY family,operation,parameter,lot_id,wf_id"""
# The supported preview contains at most 100 rows. Read explicit, ordered pages
# and reconcile the total; never treat a preview sample as the whole population.
count_sql='SELECT count(*) AS total FROM ('+SQL+') analysis'
total=query(count_sql)['rows'][0]['total']
assert total<=20000
rows=[]
for offset in range(0,total,90):
    rows.extend(query(SQL+f' LIMIT 90 OFFSET {offset}')['rows'])
assert len(rows)==total==query(count_sql)['rows'][0]['total']
assert len({(r['family'],r['operation'],r['parameter'],r['lot_id'],r['wf_id']) for r in rows})==total
report={'question':'XXXA에서 operA 에 따른 특성 변화가 관측돼?',
    'interpretation':'XXXA oper A의 Recipe 조건별 후속 oper A CD Inline 및 EPM 특성 비교',
    'period_start':'2026-09-28T00:00:00+09:00','period_basis':'현재 화면의 2026 WW40 이후를 탐색 범위로 사용',
    'catalog':catalog['rows'],'sql':SQL,'wafer_rows':rows}
(ROOT/'validation/oper-a-question-data.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'catalog':catalog['rows'],'wafer_rows':len(rows),
    'revisited_rows':sum((r['visits'] or 0)>1 for r in rows),
    'mixed_recipe_rows':sum(r['recipes']>1 for r in rows),
    'unmatched_sites':sum(r['unmatched_sites'] for r in rows),
    'last_null_sites':sum(r['sites']-r['valid_sites'] for r in rows)},ensure_ascii=False))
