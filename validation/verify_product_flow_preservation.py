"""Read-only complete metadata fingerprint. Never prints rows or credentials."""
import json
from pathlib import Path
import sys
import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]
runtime=json.loads((ROOT/'.local/runtime.json').read_text(encoding='utf-8-sig'))
assert runtime['marker']=='wafer-local-demo-v1' and runtime['port']==55432
with psycopg.connect(host='127.0.0.1',port=runtime['port'],dbname='wafer_app',user='wafer_metadata',password=runtime['app_password']) as db:
    db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    names=db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND starts_with(tablename,'app_') ORDER BY tablename").fetchall()
    result={}
    for (name,) in names:
        count,digest=db.execute(sql.SQL("SELECT count(*), md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM (SELECT md5(to_jsonb(t)::text) h FROM public.{} t) rows").format(sql.Identifier(name))).fetchone()
        result[name]={'rows':count,'digest':digest}
path=ROOT/'validation/product-flow-metadata-before.json'
if sys.argv[1]=='before':
    if path.exists():raise RuntimeError('Baseline already exists; refusing overwrite')
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
else:
    assert result==json.loads(path.read_text()),'Existing metadata changed'
    (ROOT/'validation/product-flow-metadata-after.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'tables':len(result),'rows':sum(v['rows'] for v in result.values()),'unchanged':sys.argv[1]!='before'}))
