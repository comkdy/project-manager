"""Remove only the two failed verification chats created during this task."""
import json
import urllib.request

for identity, created in [
    ('7586dfb9-0859-45f4-9416-94a3a98ab3d6', '2026-10-25 04:31:17'),
    ('a00bc1b6-41fe-40bc-a3f4-f8d78e34c6de', '2026-10-25 04:32:46'),
]:
    url = 'http://127.0.0.1:8000/api/chat/conversations/' + identity
    with urllib.request.urlopen(url) as response:
        conversation = json.load(response)
    assert conversation['created_at'].replace('T', ' ').startswith(created) and conversation['revision'] == 1
    assert conversation['project_id'] is None and not conversation['running']
    messages = conversation['messages']
    assert len(messages) == 2 and messages[0]['text'] == 'XXXA에서 operA 에 따른 특성 변화가 관측돼?'
    assert messages[1]['evaluation_cases']['status'] == 'empty'
    with urllib.request.urlopen(urllib.request.Request(url, method='DELETE')) as response:
        assert response.status == 204
print('Removed only two failed, task-created diagnostic chats; original and successful verification chat retained.')
