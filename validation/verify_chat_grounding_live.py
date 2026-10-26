"""Exercise the real configured model and saved chat without confirming an analysis."""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def call(path, body=None):
    request = urllib.request.Request('http://127.0.0.1:8000/api' + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=240) as response:
        return response.read().decode()


bootstrap = json.loads(call('/bootstrap'))
project = next(p for p in bootstrap['projects'] if p['code'] == 'XXXA')
original = json.loads(call('/chat/conversations/4805fe89-116a-4d6d-93b5-040072ccd514'))
body = {'message': 'XXXA에서 operA 에 따른 특성 변화가 관측돼?',
    'project_id': None, 'period_start': {'year': 2026, 'week': 40},
    'dataset_id': 'auto_69e8016a5d10b686efccb1e7dd7153968b3df965ea2f9612', 'save_history': True}
raw = call('/chat', body)
events = {}
for block in raw.strip().split('\n\n'):
    lines = block.splitlines()
    if len(lines) >= 2 and lines[0].startswith('event: '):
        events[lines[0][7:]] = json.loads(lines[1][6:])
(ROOT / 'validation/chat-grounding-live.json').write_text(
    json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
proposal = events.get('analysis_proposal', {})
print(json.dumps({'events': list(events), 'error': events.get('error'),
    'clarification': events.get('clarification'), 'conversation': events.get('conversation'),
    'project_id': proposal.get('project_id'), 'request': proposal.get('request'),
    'explanation': proposal.get('explanation')}, ensure_ascii=False))
assert 'error' not in events and proposal.get('status') == 'pending'
assert proposal['project_id'] == project['id']
request = proposal['request']
assert request['kind'] == 'split' and request['operation'] == 'oper A'
assert proposal['goal'] == 'describe' and request['parameter'] is request['family'] is None
assert request['recipe_a'] is request['recipe_b'] is request['start'] is request['end'] is None
assert not proposal['blockers'] and proposal['run_id'] is None
assert proposal['explanation'].startswith('해석한 범위: XXXA · 공정 oper A.')
restored = json.loads(call('/chat/conversations/' + events['conversation']['id']))
assert restored['project_id'] is None
assert restored['messages'][-1]['analysis_proposal'] == proposal
detail = json.loads(call('/analytics/proposals/' + proposal['proposal_id'] + '?project_id=' + str(project['id'])))
assert detail == proposal
assert json.loads(call('/chat/conversations/4805fe89-116a-4d6d-93b5-040072ccd514')) == original
print('PASS: actual model, global chat project scope, restore, proposal review; original conversation unchanged.')
