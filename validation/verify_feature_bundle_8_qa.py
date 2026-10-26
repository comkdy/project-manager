"""Exercise the real disposable app, including schedule evidence, controls and approvals."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import time
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8001/api'


def api(path,body=None):
    request=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=40) as response:return json.load(response)


def until(read, condition):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        value=read()
        if condition(value):return value
        time.sleep(.3)
    raise AssertionError('QA condition did not complete')


def complete(identity):
    run=until(lambda:api('/analytics/runs/'+identity),lambda r:r['status'] not in {'queued','running','cancelling'})
    assert run['status']=='completed',run['error']
    return run


def save(identity,name):
    p=api('/analytics/schedules/preview',{'source_run_id':identity,'interval_hours':1,'workspace_token':token})
    return dict(source_run_id=identity,name=name,interval_hours=1,workspace_token=token,
        expected_run_revision=p['source_run_revision'],first_run_at=p['first_run_at'])


def schedules():return api('/analytics/schedules')
def current(identity):return next(s for s in schedules()['items'] if s['id']==identity)
def due(identity):
    (ROOT/'.local/feature-bundle-8-command.json').write_text(json.dumps({'schedule_id':identity,'action':'due'}))
def comparison(schedule,run):return api(f'/analytics/schedules/{schedule}/runs/{run}/comparison')


ids=json.loads((ROOT/'.local/feature-bundle-8-qa-ids.json').read_text())
token=api('/bootstrap')['workspace_token']
schedule=api('/analytics/schedules',save(ids['confirmed_id'],'공통 PARA · 정기 분석 이력'))
gate=ROOT/'.local/feature-bundle-8-analysis.hold'
try:
    gate.write_text('Disposable QA only')
    due(schedule['id'])
    queued=until(lambda:current(schedule['id']),lambda s:bool(s['last_run_id']))
    running=until(lambda:api('/analytics/runs/'+queued['last_run_id']),lambda r:r['status']=='running')
    target={'kind':'analysis_run','id':running['id']}
    api('/workspace/activity/actions',{'target':target,'action':'cancel','expected_revision':running['revision'],'workspace_token':token})
finally:gate.unlink(missing_ok=True)
cancelled=until(lambda:api('/analytics/runs/'+running['id']),lambda r:r['status']=='cancelled')
until(schedules,lambda s:bool(s['notifications']))
assert cancelled['result'] is None and not cancelled['source_watermark']
retry=api('/workspace/activity/actions',{'target':target,'action':'retry','expected_revision':cancelled['revision'],'workspace_token':token})
child=complete(retry['target']['id'])
assert child['request']==cancelled['request'] and child['scheduled_for']==cancelled['scheduled_for']
assert child['schedule_id']==schedule['id'] and not current(schedule['id'])['enabled']
history=api('/analytics/schedules/'+schedule['id']+'/history')
assert {r['id'] for r in history['items']}=={ids['confirmed_id'],cancelled['id'],child['id']}
comp=comparison(schedule['id'],child['id'])
assert comp['comparable'] and comp['previous']['id']==ids['confirmed_id']

# Approve the real question draft and explicitly enter numerical criteria as before.
question=api('/semantic/collection/questions/'+ids['question_id']);draft=question['draft']
api('/semantic/collection/drafts/'+draft['id']+'/review',{'expected_revision':draft['revision'],'decision':'approve','note':'실행 이력·결과 비교와 승인 근거 검증'})
original=api('/analytics/runs/'+ids['run_id']);definition=original['questions'][0]['approved_definition']
ref={'item_id':definition['item_id'],'version':definition['version']}
request={k:v for k,v in original['request'].items() if not k.startswith('_')}
request.update(min_samples=3,absolute_delta=.5,knowledge_refs=[ref])
resumed=complete(api('/analytics/runs',request)['id'])
latest=current(schedule['id'])
api('/analytics/schedules/'+schedule['id']+'/actions',{'action':'replace','expected_revision':latest['revision'],'workspace_token':token,'replacement':save(resumed['id'],schedule['name'])})
assert current(schedule['id'])['request']['knowledge_refs']==[ref]
after=comparison(schedule['id'],child['id'])
assert after['previous']['id']==comp['previous']['id'] and after['rows']==comp['rows']
assert api('/semantic/collection/questions/'+ids['question_id'])['draft']['status']=='approved'

# A project window moving beyond the demo measurement range produces real missing-series evidence.
end=datetime.fromisoformat(request['current_end'].replace('Z','+00:00'))
project_request={**request,'project_id':ids['project_id'],
    'baseline_start':(end-timedelta(days=4)).isoformat(),'baseline_end':(end-timedelta(days=2)).isoformat(),
    'current_start':(end-timedelta(days=2)).isoformat(),'current_end':end.isoformat()}
project_run=complete(api('/analytics/runs',project_request)['id'])
project=api('/analytics/schedules',save(project_run['id'],'프로젝트 · 최근 4일 변화'))
due(project['id'])
state=until(lambda:api('/analytics/schedules?project_id='+str(ids['project_id'])),lambda s:bool(s['notifications']))
assert len(state['items'])==1 and state['items'][0]['id']==project['id']
scheduled=complete(state['items'][0]['last_run_id'])
assert scheduled['request']['_project_scope']==project_run['request']['_project_scope']
assert scheduled['request']['knowledge_refs']==[ref]
notice=state['notifications'][0]
project_comp=comparison(project['id'],notice['run_id'])
assert notice['comparison_run_id']==project_run['id']==project_comp['previous']['id']
assert project_comp['comparable'] and project_comp['notification_change_count']>0
assert all(r['change']=='removed' and r['notification_change'] for r in project_comp['rows'])
api('/analytics/schedules/notifications/'+notice['id']+'/ack',{'workspace_token':token})
assert comparison(project['id'],notice['run_id'])['rows']==project_comp['rows']
ids.update(schedule_id=schedule['id'],retry_id=child['id'],cancelled_id=cancelled['id'],project_schedule_id=project['id'],project_run_id=scheduled['id'])
(ROOT/'.local/feature-bundle-8-qa-ids.json').write_text(json.dumps(ids),encoding='utf-8')
result=dict(cancelled_result_discarded=True,retry_lineage_and_period_retained=True,
    history_contains_original_cancelled_and_retry=True,pinned_comparison_survives_condition_replacement=True,
    approval_retained=True,resumed_row_count=resumed['result']['row_count'],project_scope_and_approved_reference_retained=True,
    project_filter_isolated=True,notification_evidence_exact=True,ack_preserves_evidence=True,
    project_changed_series=project_comp['notification_change_count'],common_cards=len(api('/dashboards')['cards']))

# Save all existing flow views, then back up and restore without changing them.
paths=['/analytics/schedules', '/analytics/schedules/'+schedule['id']+'/history',
    '/analytics/schedules/'+project['id']+'/history',
    '/analytics/schedules/'+schedule['id']+'/runs/'+child['id']+'/comparison',
    '/analytics/schedules/'+project['id']+'/runs/'+notice['run_id']+'/comparison',
    '/semantic/collection/questions/'+ids['question_id'], '/analytics/runs/'+resumed['id'],
    '/dashboards', '/bootstrap']
def stable(value):
    if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in {'generated_at','can_run','next_run_in_seconds'}}
    if isinstance(value,list):return [stable(v) for v in value]
    return value
before=[stable(api(path)) for path in paths]
diagnostic_before=api('/admin/operations')
checks={item['id']:item for item in diagnostic_before['checks']}
assert diagnostic_before['workspace_token']==token
assert checks['metadata']['status']==checks['source']['status']=='ok'
assert checks['backup']['status']=='attention'
assert checks['off_device']['status']=='unknown'
assert all(checks['worker_'+name]['status'] in {'ok','info'} for name in ('analysis','collection','schedule'))
assert [stable(api(path)) for path in paths]==before,'Diagnostics changed existing workflows'
backup_request={'workspace_token':token,'request_id':uuid.uuid4().hex}
backup=api('/admin/recovery',backup_request)
def recovery_state():return api('/admin/recovery')
def checked(identity):
    state=until(recovery_state,lambda s:not s['busy'] and any(r['id']==identity and r['status']!='running' for r in s['items']))
    item=next(r for r in state['items'] if r['id']==identity)
    assert item['status']=='verified',item
    assert all(check['status']=='passed' for check in item['checks'])
    return item
verified=checked(backup['id'])
assert verified['table_count']>=20 and verified['row_count']>0
api('/admin/recovery/'+backup['id']+'/verify',{'workspace_token':token,'request_id':uuid.uuid4().hex})
checked(backup['id'])
assert api('/admin/recovery',backup_request)['id']==backup['id']
assert recovery_state()['backup_count']==1
diagnostic_after=api('/admin/operations')
checks={item['id']:item for item in diagnostic_after['checks']}
assert checks['backup']['status']=='ok'
assert checks['off_device']['status']=='unknown'
assert [stable(api(path)) for path in paths]==before,'Backup changed an existing workflow'
result.update(real_backup_and_restore_verified=True,reverification_verified=True,creation_retry_deduplicated=True,
    operations_read_only=True,source_and_worker_observation_verified=True,backup_status_updates_after_verification=True,
    original_analysis_questions_approvals_schedules_history_evidence_unchanged=True,
    backed_up_tables=verified['table_count'],backed_up_rows=verified['row_count'])
ids['backup_id']=backup['id']
(ROOT/'.local/feature-bundle-8-qa-ids.json').write_text(json.dumps(ids),encoding='utf-8')
(ROOT/'validation/feature-bundle-8-qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result))
