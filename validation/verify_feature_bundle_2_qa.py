"""Read-only assertions after the documented browser journey on disposable QA."""
import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def get(path):
    with urlopen('http://127.0.0.1:8001/api/' + path, timeout=20) as response:
        return json.load(response)


before = json.loads((ROOT / 'validation/feature-bundle-2-qa-before.json').read_text(encoding='utf-8-sig'))
assert before['count'] == 1
conversations = get('chat/conversations')['items']
analysis = next(get('chat/conversations/' + c['id']) for c in conversations if c['title'].startswith('EPM'))
sql = next(get('chat/conversations/' + c['id']) for c in conversations if c['title'].startswith('검증지표'))
plan = next(m['analysis_proposal'] for m in analysis['messages'] if m.get('analysis_proposal'))
assert plan['status'] == 'confirmed'
assert plan['request']['min_samples'] == 7 and plan['request']['absolute_delta'] == 0.2
assert plan['request']['baseline_start'] == '2026-09-01T00:00:00Z'
assert plan['request']['current_end'] == '2026-10-01T00:00:00Z'
assert plan['request']['retest_policy'] == 'all_events'
runs = get('analytics/runs')
assert len(runs) == 3  # seed + one confirmed proposal + one manual resumption
confirmed = next(r for r in runs if r['id'] == plan['run_id'])
assert confirmed['status'] == 'completed' and confirmed['request'] == plan['request']
seed = next(r for r in runs if r['id'] in before['ids'])
resumed = next(r for r in runs if r['request']['knowledge_refs'])
assert resumed['status'] == 'completed'
for field in ('baseline_start', 'baseline_end', 'current_start', 'current_end', 'retest_policy'):
    assert resumed['request'][field] == seed['request'][field]
assert datetime.fromisoformat(resumed['request']['baseline_end']).microsecond == 500000
assert resumed['request']['min_samples'] == 3 and resumed['request']['absolute_delta'] == 0.5
answers = [m for m in sql['messages'] if m['role'] == 'assistant']
assert len(answers) == 3
assert answers[0]['knowledge']['resolution'] == 'needs_clarification'
assert len(answers[0]['knowledge']['clarifications'][0]['options']) == 2
for message in answers[1:]:
    knowledge = message['knowledge']
    assert knowledge['resolution'] == 'resolved'
    assert len(knowledge['sources']) == 1
    assert knowledge['sources'][0]['name'] == '검증 평균 기준'
    assert knowledge['sources'][0]['version'] == 1
assert answers[1]['applied'] is True
assert answers[1]['knowledge']['sources'] == answers[2]['knowledge']['sources']
assert sql['context']['knowledge_context_id'] == answers[2]['knowledge']['context_id']
assert get('datasets/qa-shared/latest')['version'] == 1  # impact preview did not save
result = {
    'passed': True,
    'model_mode': 'deterministic local fixture; no external LLM calls',
    'runs_before_confirmation': before['count'],
    'runs_after_confirmation_and_manual_resume': len(runs),
    'confirmed_run_id': confirmed['id'],
    'confirmed_conditions': plan['request'],
    'resumed_run_id': resumed['id'],
    'original_precise_periods_preserved': True,
    'approved_knowledge_ref_count': len(resumed['request']['knowledge_refs']),
    'ambiguous_definition_choices': 2,
    'selected_definition': answers[2]['knowledge']['sources'][0],
    'sql_proposal_saved_as_dataset': True,
    'followup_context_and_restored_provenance_preserved': True,
    'impact_preview_dataset_version_unchanged': True,
}
(ROOT / 'validation/feature-bundle-2-qa-result.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('All bundle 2 browser-journey readback assertions passed.')
