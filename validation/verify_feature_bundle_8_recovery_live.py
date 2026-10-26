"""Read-only metadata digest before/after the UI's live backup; no secrets emitted."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import recovery  # noqa: E402

url,runtime=recovery.configuration()
assert url.database=='wafer_app'
with recovery.connect(url) as connection:
    connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    tables=recovery.inventory(connection)
    issues=recovery.relation_issues(connection)
snapshot={'content_sha256':hashlib.sha256(json.dumps(tables,sort_keys=True).encode()).hexdigest(),
    'table_count':len(tables),'row_count':sum(t['rows'] for t in tables.values()),'relation_issues':issues}
path=ROOT/'validation/feature-bundle-8-metadata-before.json'
if sys.argv[1]=='before':path.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
else:
    assert snapshot==json.loads(path.read_text(encoding='utf-8')),'Metadata changed during backup verification'
    with urllib.request.urlopen('http://127.0.0.1:8000/api/admin/recovery',timeout=10) as response:state=json.load(response)
    assert not state['busy'] and state['items']
    latest=state['items'][0]
    assert latest['status'] in {'verified','attention'},latest['message']
    with recovery.admin_connect(runtime) as admin:
        remaining=admin.execute("SELECT count(*) FROM pg_database WHERE shobj_description(oid,'pg_database')=%s",('project-manager-recovery:'+latest['id'],)).fetchone()[0]
    assert remaining==0
    snapshot.update(unchanged=True,backup_id=latest['id'],backup_status=latest['status'],checks=latest['checks'],temporary_databases_remaining=remaining)
    (ROOT/'validation/feature-bundle-8-metadata-after.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(snapshot,ensure_ascii=False))
