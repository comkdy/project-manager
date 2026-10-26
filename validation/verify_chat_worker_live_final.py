"""Inspect actual model evidence and review links; defer superseded test questions."""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def call(path, body=None):
    request = urllib.request.Request('http://127.0.0.1:8000/api' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


records = {method: json.loads((ROOT / f'validation/chat-worker-{method}-live.json').read_text(encoding='utf-8'))
           for method in ('correlation', 'group_compare', 'ml_regression', 'natural_ml', 'ambiguous')}
ml = records['ml_regression']
review = call('/semantic/collection/questions/' + ml['pending_review']['question_id'])
draft = call('/semantic/collection/drafts/' + ml['pending_review']['draft_id'])
assert draft['status'] == 'pending' and draft['item_id'] is None
assert ml['completed']['run_id'] in draft['proposal']['content']['details']['evidence']
waiting = records['ambiguous']['events']['data_analysis']
assert waiting['status'] == 'needs_input' and waiting['result'] is None and waiting['settings'] is None
assert waiting['plan']['columns'] == [] and waiting['plan']['row_basis'] is None
assert records['natural_ml']['completed']['dataset']['project_id'] == 1000004
assert any(c['evidence']['reference'].startswith('case:') for c in records['natural_ml']['completed']['plan']['columns'])

diagnostic = json.loads((ROOT / 'validation/chat-worker-plan-diagnostic.json').read_text(encoding='utf-8'))


def no_rows(value):
    if isinstance(value, dict):
        assert not {'rows', 'sample_rows', 'preview', 'query_result', 'records', 'dataframe'} & value.keys()
        for child in value.values(): no_rows(child)
    elif isinstance(value, list):
        for child in value: no_rows(child)


facts = []
for record in diagnostic:
    request = record['request']
    context = json.loads(request['messages'][0]['content'].split('\nRegistered context:\n', 1)[1])
    assert set(context) == {'data_analysis_context'}
    no_rows(context)
    facts = context['data_analysis_context']['facts']
    for fact in facts:
        if fact['kind'] not in {'user', 'clarification'}: no_rows(json.loads(fact['text']))
assert sum(f['kind'] == 'case' for f in facts) >= 1

# Only redundant questions created by this validation are deferred. No deletion,
# answer, knowledge publication, or change to an existing user request occurs.
superseded = ['a2ecfa13-9c30-4011-83e6-402135c097d0', '3ef6414e-46f6-4b17-a869-66e7d5a20363']
for identity in superseded:
    question = call('/semantic/collection/questions/' + identity)
    assert question['analysis_setup']['run_id'] in {'693dfec0-2e6c-4bb3-b089-77f96bdcdd1a', 'a87312b3-7e9f-4634-b7fb-7f17e3c9b68f'}
    if question['status'] == 'open':
        call('/semantic/collection/questions/' + identity + '/defer', {'expected_revision': question['revision'], 'days': 90})
    assert call('/semantic/collection/questions/' + identity)['status'] == 'deferred'

result = {'model': diagnostic[0]['request']['model'], 'raw_measurement_rows_sent': False,
    'planning_fact_counts': {kind: sum(f['kind'] == kind for f in facts) for kind in {'user', 'dataset', 'knowledge', 'case'}},
    'completed_methods': {key: records[key]['completed']['run_id'] for key in records if key != 'ambiguous'},
    'clarification_question': records['ambiguous']['question']['id'],
    'pending_review': ml['pending_review'], 'superseded_test_questions_deferred': superseded}
(ROOT / 'validation/chat-worker-live-final.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
