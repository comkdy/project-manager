"""Check the real temporary browser exercise without changing live metadata."""
import json
from pathlib import Path
import urllib.request

BASE = 'http://127.0.0.1:8001/api'
ROOT = Path(__file__).resolve().parents[1]


def get(path):
    return json.load(urllib.request.urlopen(BASE+path, timeout=30))


b = get('/bootstrap')
runs = get('/analytics/runs')
failed = next(r for r in runs if r['status']=='failed' and r['retry_run_id'])
child = get('/analytics/runs/'+failed['retry_run_id'])
assert child['status']=='cancelled' and child['result'] is None and not child['source_watermark']
assert child['retry_of']==failed['id'] and child['request']==failed['request']
q = child['questions'][0]
assert q['approved_definition']['content']['description']=='최소 유효 측정 수는 3개, 평균 변화량 기준은 0.5입니다.'
question = get('/semantic/collection/questions/'+q['id'])
assert question['draft']['status']=='approved' and len(question['answers'])==1
resumed = next(get('/analytics/runs/'+r['id']) for r in runs if r['request'].get('knowledge_refs'))
assert resumed['status']=='completed' and resumed['request']['min_samples']==3 and resumed['request']['absolute_delta']==0.5
assert resumed['result']['row_count']>0 and resumed['request']['knowledge_refs'][0]['item_id']==q['approved_definition']['item_id']
project_run = next(r for r in runs if r['request'].get('project_id')==1000999 and not r['retry_of'])
assert project_run['status']=='cancelled' and project_run['result'] is None and project_run['started_at'] is None
project_retry = get('/analytics/runs/'+project_run['retry_run_id'])
assert project_retry['status']=='completed' and project_retry['request']==project_run['request']
activity = get('/workspace/activity')
proposal = next(i for i in activity['items'] if i['kind']=='analysis_proposal')
assert proposal['status']=='cancelled' and not proposal['controls']['can_cancel']
assert get('/analytics/proposals/'+proposal['target']['id'])['status']=='cancelled'
body = {'target':{'kind':'analysis_run','id':failed['id']},'action':'retry',
        'expected_revision':failed['revision']-1,'workspace_token':b['workspace_token']}
request = urllib.request.Request(BASE+'/workspace/activity/actions',data=json.dumps(body).encode(),headers={
    'Content-Type':'application/json','Origin':'http://127.0.0.1:8001','X-Workspace-Token':b['workspace_token']})
assert json.load(urllib.request.urlopen(request))['target']['id']==child['id']
assert len(get('/analytics/runs'))==len(runs)
summary = {'analysis_runs':len(runs),'retry_link_preserved':True,'duplicate_retry_created_no_run':True,
    'running_cancel_discarded_result':True,'queued_project_cancel_never_started':True,'project_retry_completed_in_same_scope':True,
    'proposal_cancel_created_no_run':True,'question_answers':len(question['answers']),
    'approval_preserved':True,'resumed_status':resumed['status'],'resumed_row_count':resumed['result']['row_count'],
    'common_dashboard_cards':len(get('/dashboards')['cards'])}
(ROOT/'validation/feature-bundle-4-qa.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary))
