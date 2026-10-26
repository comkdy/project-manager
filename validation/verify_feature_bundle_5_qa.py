"""Verify real disposable-app persistence after browser cancel/retry, then approval and rolling project execution."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8001/api'


def api(path,body=None):
    request=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=40) as response:return json.load(response)


def complete(identity):
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        run=api('/analytics/runs/'+identity)
        if run['status'] not in {'queued','running','cancelling'}:
            assert run['status']=='completed',run['error']
            return run
        time.sleep(.3)
    raise AssertionError('Analysis did not finish')


def save_body(identity,name):
    preview=api('/analytics/schedules/preview',{'source_run_id':identity,'interval_hours':1,'workspace_token':token})
    return dict(source_run_id=identity,name=name,interval_hours=1,workspace_token=token,
        expected_run_revision=preview['source_run_revision'],first_run_at=preview['first_run_at'])


ids=json.loads((ROOT/'.local/feature-bundle-5-qa-ids.json').read_text())
token=api('/bootstrap')['workspace_token']
state=api('/analytics/schedules')
schedule=state['items'][0]
assert not schedule['enabled']
child=complete(schedule['last_run_id'])
parent=api('/analytics/runs/'+child['retry_of'])
assert parent['status']=='cancelled' and parent['result'] is None and not parent['source_watermark']
assert child['request']==parent['request'] and child['schedule_id']==parent['schedule_id']==schedule['id']
assert child['scheduled_for']==parent['scheduled_for']
assert len(state['notifications'])==1 and state['unread_count']==0

# The seed question already has a grounded answer and a review draft; approve through the normal API.
question=api('/semantic/collection/questions/'+ids['question_id'])
draft=question['draft']
api('/semantic/collection/drafts/'+draft['id']+'/review',{'expected_revision':draft['revision'],
    'decision':'approve','note':'정기 분석과 승인 근거 연결 검증'})
original=api('/analytics/runs/'+ids['run_id'])
definition=original['questions'][0]['approved_definition']
ref={'item_id':definition['item_id'],'version':definition['version']}
request={k:v for k,v in original['request'].items() if not k.startswith('_')}
request.update(min_samples=3,absolute_delta=.5,knowledge_refs=[ref])
resumed=complete(api('/analytics/runs',request)['id'])
updated=api('/analytics/schedules/'+schedule['id']+'/actions',{'action':'replace','expected_revision':schedule['revision'],
    'workspace_token':token,'replacement':save_body(resumed['id'],schedule['name'])})
assert updated['enabled'] and updated['request']['knowledge_refs']==[ref]
assert api('/semantic/collection/questions/'+ids['question_id'])['draft']['status']=='approved'

# A short, real project window shifts beyond the sample source's last measurement.
end=datetime.fromisoformat(request['current_end'].replace('Z','+00:00'))
project_request={**request,'project_id':ids['project_id'],
    'baseline_start':(end-timedelta(days=4)).isoformat(),'baseline_end':(end-timedelta(days=2)).isoformat(),
    'current_start':(end-timedelta(days=2)).isoformat(),'current_end':end.isoformat()}
project_run=complete(api('/analytics/runs',project_request)['id'])
assert project_run['result']['series']
project_schedule=api('/analytics/schedules',save_body(project_run['id'],'검증 프로젝트 · 최근 4일'))
(ROOT/'.local/feature-bundle-5-command.json').write_text(json.dumps({'schedule_id':project_schedule['id'],'action':'due'}))
deadline=time.monotonic()+45
while time.monotonic()<deadline:
    scoped=api('/analytics/schedules?project_id='+str(ids['project_id']))
    assert [s['id'] for s in scoped['items']]==[project_schedule['id']]
    latest=scoped['items'][0]
    if latest['last_run_id'] and scoped['notifications']:
        projected=complete(latest['last_run_id'])
        break
    time.sleep(.5)
else:raise AssertionError('Scheduled project run or notice missing')
assert projected['request']['_project_scope']==project_run['request']['_project_scope']
assert projected['request']['knowledge_refs']==[ref]
assert projected['result']['row_count']==0 and not projected['result']['series']
assert scoped['notifications'][0]['kind']=='changed' and latest['enabled']
result=dict(cancelled_result_discarded=True,retry_kept_exact_period_and_schedule=True,
    cancelled_notice_acknowledged=True,approval_retained=True,existing_schedule_updated_with_approved_conditions=True,
    resumed_row_count=resumed['result']['row_count'],project_scope_retained=True,
    project_filter_isolated=True,rolling_window_empty_source_notice=True,
    common_dashboard_cards=len(api('/dashboards')['cards']),schedules=len(api('/analytics/schedules')['items']))
(ROOT/'validation/feature-bundle-5-qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result))
