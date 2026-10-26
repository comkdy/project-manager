"""QA-only concurrent edits and final assertions against the disposable server."""
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8001/api'


def request(path, body=None, method=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Workspace-Token'] = token
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
        headers=headers, method=method or ('POST' if body is not None else 'GET'))
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


token = None
bootstrap = request('/bootstrap')
token = bootstrap['workspace_token']
ids = json.loads((ROOT / '.local/feature-bundle-qa-ids.json').read_text())
assert any(p['id'] == ids['project_id'] and p['name'] == '검증 프로젝트' for p in bootstrap['projects'])
mode = sys.argv[1]
if mode == 'dataset-conflict':
    old = request('/datasets/qa-shared/latest')
    assert old['version'] == 1
    spec = old['spec'] | {'raw_sql': 'SELECT lot_cd, value, end_dt_tm FROM public.epm_data -- 다른 편집자의 변경'}
    result = request('/datasets/qa-shared/versions', {'expected_version': 1, 'name': old['name'], 'project_id': None, 'spec': spec})
    print(json.dumps({'dataset_version': result['version']}, ensure_ascii=False))
elif mode == 'board-conflict':
    board = request('/dashboards')
    assert board['id'] == 'common'
    board['cards'][1]['title'] = '다른 편집자가 바꾼 행수'
    result = request('/dashboards/common', {k: board[k] for k in ('version', 'cards', 'layout')}, 'PUT')
    print(json.dumps({'board_version': result['version']}, ensure_ascii=False))
elif mode == 'knowledge-conflict':
    item = next(i for i in request('/semantic/items')['items'] if i['content']['name'] == '검증 평균 기준')
    content = item['content'] | {'description': '다른 편집자가 추가한 산술평균 정의', 'change_note': '동시 편집 검증'}
    result = request('/semantic/items/' + item['id'] + '/versions', {'expected_revision': item['revision'], 'content': content})
    print(json.dumps({'definition_version': result['version']}, ensure_ascii=False))
elif mode == 'verify':
    activity = request('/workspace/activity')
    board = request('/dashboards')
    dataset = request('/datasets/qa-shared/latest')
    assert activity['workspace_token'] == token
    assert dataset['version'] == 3
    assert '복구 검증' in dataset['spec']['raw_sql']
    assert {c['title'] for c in board['cards']} == {'복구 검증 평균', '다른 편집자가 바꾼 행수'}
    assert all('result' not in item for item in activity['items'])
    assert any(item['status'] == 'failed' for item in activity['items'])
    item = next(i for i in request('/semantic/items')['items'] if i['content']['name'] == '검증 평균 기준')
    assert '복구된 정의' in item['content']['description'] and item['version'] == 3
    runs = request('/analytics/runs')
    assert len(runs) == 4 and sum(r['status'] == 'completed' for r in runs) == 3
    assert any(r['request'].get('knowledge_refs') for r in runs)
    assert not any(i['kind'] == 'analysis_proposal' for i in activity['items'])
    question = request('/semantic/collection/questions/' + ids['question_id'])
    assert question['status'] == 'answered' and question['draft']['status'] == 'approved'
    assert not request('/workspace/activity?project_id=' + str(ids['project_id']))['items']
    result = {'activity_items': len(activity['items']), 'dataset_version': dataset['version'],
        'board_version': board['version'], 'card_titles': [c['title'] for c in board['cards']],
        'knowledge_version': item['version'], 'analysis_runs': len(runs),
        'approved_question': question['status'], 'checks': 'passed'}
    (ROOT / 'validation/feature-bundle-3-api-checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
else:
    raise ValueError('Unknown QA action')
