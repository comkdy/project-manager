"""Read-only live checks, plus requests to explicitly retired endpoints."""
import json
from pathlib import Path
import urllib.request
import urllib.error

root=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:8000'
def get(path):
    with urllib.request.urlopen(base+path,timeout=20) as response:return json.load(response)
health=get('/api/health');assert health['status']=='ok'
bootstrap=get('/api/bootstrap');board=get('/api/dashboards')
for path in ['/api/admin/recovery','/api/admin/recovery/retired/verify','/api/admin/operations']:
    for method in ['GET','POST']:
        request=urllib.request.Request(base+path,method=method,data=b'{}' if method=='POST' else None,headers={'Content-Type':'application/json'})
        try:urllib.request.urlopen(request,timeout=20)
        except urllib.error.HTTPError as error:assert error.code in (404,405),(path,method,error.code)
        else:raise AssertionError('Retired endpoint remains reachable')
with urllib.request.urlopen(base+'/') as response:html=response.read()
assert html==(root/'frontend/dist-product-flow/index.html').read_bytes()
result={'health':'ok','projects':len(bootstrap['projects']),'datasets':len(bootstrap['datasets']),'common_cards':len(board['cards']),'backup_and_operations_unreachable':True,'final_build_served':True}
(root/'validation/product-flow-live.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
