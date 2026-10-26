"""Only the requested approval and its validation chat may change metadata."""
import json
from approve_chat_worker_result import ROOT, DRAFT, QUESTION, snapshot

before = json.loads((ROOT / 'validation/chat-worker-approval-metadata-before.json').read_text(encoding='utf-8'))
after = snapshot()
approved = json.loads((ROOT / 'validation/chat-worker-result-approved.json').read_text(encoding='utf-8'))['approval']['item_id']
modified = {}
added = {}
allowed_modified = {'app_semantic_questions': QUESTION, 'app_semantic_collected_drafts': DRAFT}
allowed_added = {'app_audit', 'app_chat_conversations', 'app_chat_turns', 'app_semantic_contexts', 'app_semantic_items', 'app_semantic_versions'}
for table, rows in before.items():
    assert set(rows) <= set(after[table]), ('deleted metadata', table)
    changed = [key for key in rows if rows[key] != after[table][key]]
    if changed:
        assert table in allowed_modified and all(json.loads(key) == [allowed_modified[table]] for key in changed), (table, changed)
        modified[table] = [json.loads(key) for key in changed]
    extra = set(after[table]) - set(rows)
    if extra:
        assert table in allowed_added, (table, extra)
        added[table] = [json.loads(key) for key in sorted(extra)]
assert added['app_semantic_items'] == [[approved]]
assert added['app_semantic_versions'] == [[approved, 1]]
assert before['app_analysis_runs'] == after['app_analysis_runs']
result = {'existing_rows': sum(len(rows) for rows in before.values()), 'tables': len(before),
          'only_requested_approval_modified': True, 'existing_analysis_results_unchanged': True,
          'modified': modified, 'added': added}
(ROOT / 'validation/chat-worker-approval-preservation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
