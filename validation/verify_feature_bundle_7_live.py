"""Read-only live-workspace snapshot and post-restart comparison."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8000/api'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.load(response)


bootstrap = get('/bootstrap')
boards = [get('/dashboards')]
scopes = [bootstrap]
for project in bootstrap['projects']:
    scopes.append(get('/bootstrap?project_id=' + str(project['id'])))
    boards.append(get('/dashboards?project_id=' + str(project['id'])))
content = {'projects': bootstrap['projects'], 'datasets': [s['datasets'] for s in scopes], 'boards': boards}
digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
summary = {'projects': len(bootstrap['projects']), 'common_datasets': len(bootstrap['datasets']),
    'common_cards': len(boards[0]['cards']), 'content_sha256': digest}
path = ROOT / 'validation/feature-bundle-7-live-before.json'
if sys.argv[1] == 'before':
    path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
else:
    assert summary == json.loads(path.read_text()), 'Business definitions changed during verification'
    activity = get('/workspace/activity')
    assert activity['workspace_token'] == bootstrap['workspace_token']
    assert activity['project_id'] is None
    summary.update(health=get('/health'), activity_items=len(activity['items']), unchanged=True)
    (ROOT / 'validation/feature-bundle-7-live-after.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
