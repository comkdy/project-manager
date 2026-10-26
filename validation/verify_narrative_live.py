"""Actual app + configured model, approved synthetic evidence, no chat history write."""
import json
import sys
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
state=json.loads((ROOT/'validation/live-case-state.json').read_text(encoding='utf-8'))
body={'message':'XXXA의 oper B Recipe·Inline MAIN과 EPM PARA3에 대해 승인된 사례와 ML 결과를 바탕으로 설명해줘. 어떤 항목이 중요하고 다음에는 무엇을 확인하면 좋을까? 수치의 의미를 자연어로 설명하고, 인과관계는 단정하지 말고 근거와 한계도 알려줘.',
      'project_id':state['project_id'],'dataset_id':state['dataset_apply']['dataset_id'],'save_history':False}
if '--save-history' in sys.argv:
    body['save_history']=True
request=urllib.request.Request('http://127.0.0.1:8000/api/chat',data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=240) as response:
    raw=response.read().decode()
events={}
for block in raw.strip().split('\n\n'):
    lines=block.splitlines()
    if len(lines)>=2 and lines[0].startswith('event: '):
        events[lines[0][7:]]=json.loads(lines[1][6:])
answer=events.get('evaluation_cases',{})
output='narrative-chat-saved.json' if body['save_history'] else 'narrative-chat-live.json'
(ROOT/'validation'/output).write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'events':list(events),'conversation':events.get('conversation'),'error':events.get('error'),
    'status':answer.get('narrative',{}).get('status'),
    'explanation':{k:[s['text'] for s in answer.get('narrative',{}).get(k,[])] for k in ('findings','next_checks','caveats')}},ensure_ascii=False))
assert 'error' not in events and answer.get('narrative',{}).get('status')=='ready'
assert {state['case_approval']['item_id'],state['result_review']['item_id']}<={c['id'] for c in answer['cases']}
if body['save_history']:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/chat/conversations/'+events['conversation']['id']) as response:
        restored=json.load(response)
    assert restored['messages'][-1]['evaluation_cases']['narrative']==answer['narrative']
    print('Saved conversation restored with identical, revalidated explanation.')
