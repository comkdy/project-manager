"""Real app chat retrieves/cites the newly approved result; no raw measurement rows."""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.assistant import ChatRequest
from app.database_settings import load_active_source
from app.evaluation_chat import evaluation_answer
from app.evaluation_narrative import evidence_payload
from app.llm_settings import llm_configuration

state = json.loads((ROOT / 'validation/chat-worker-result-approved.json').read_text(encoding='utf-8'))
reference = state['approval']['item_id']
question = 'XXXA의 oper B Recipe와 Inline MAIN 중 EPM PARA3 예측에 중요하게 볼 항목은 무엇이고, 다음에는 무엇을 확인하면 좋을까? 승인된 ML 분석 결과와 평가 사례를 근거로 설명해줘. 새로 계산하지 말고, 근거의 조회 시점과 한계도 알려줘. 인과관계는 단정하지 마.'
body = {'message': question, 'project_id': state['project_id'], 'dataset_id': state['dataset']['id'], 'save_history': True}
load_active_source()
base, model = llm_configuration()
assert urllib.parse.urlsplit(base).hostname == 'openrouter.ai'
request = ChatRequest.model_validate(body)
payload = evidence_payload(evaluation_answer(request))


def assert_no_rows(value):
    if isinstance(value, dict):
        assert not {'rows', 'sample_rows', 'raw_rows', 'preview', 'query_result', 'records', 'dataframe'} & value.keys()
        for child in value.values(): assert_no_rows(child)
    elif isinstance(value, list):
        for child in value: assert_no_rows(child)


assert_no_rows(payload)
assert any(s['case_ref']['id'] == reference and s['kind'] == 'approved_computation'
           and s['execution']['run_id'] == state['run_id'] for s in payload['sources'])
# The explanation stage sends this bounded evidence object only. No source query,
# samples or prior chat history are included by ModelAdapter for that stage.
(ROOT / 'validation/chat-worker-approved-evidence.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
http_request = urllib.request.Request('http://127.0.0.1:8000/api/chat', data=json.dumps(body, ensure_ascii=False).encode(),
                                      headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(http_request, timeout=240) as response:
    raw = response.read().decode()
events = {b.splitlines()[0][7:]: json.loads(b.splitlines()[1][6:]) for b in raw.strip().split('\n\n') if b.startswith('event:')}
output = ROOT / 'validation/chat-worker-approved-knowledge-chat.json'
output.write_text(json.dumps({'model': model, 'question': question, 'events': events}, ensure_ascii=False, indent=2), encoding='utf-8')
assert 'error' not in events and 'evaluation_cases' in events and 'data_analysis' not in events, events
answer = events['evaluation_cases']
assert answer.get('narrative', {}).get('status') == 'ready', answer.get('narrative')
matched = next(c for c in answer['cases'] if c['id'] == reference)
assert matched['version'] == 1 and matched['execution_evidence']['run_id'] == state['run_id']
statements = [s for key in ('findings', 'next_checks', 'caveats') for s in answer['narrative'][key]]
citations = [c for s in statements for c in s['citations']]
assert any(c['case_ref']['id'] == reference and c['field'] == 'analysis_result' for c in citations)
assert all(c['quote'] and c['case_ref']['id'] in {v['id'] for v in answer['cases']} for c in citations)
with urllib.request.urlopen('http://127.0.0.1:8000/api/chat/conversations/' + events['conversation']['id']) as response:
    restored = json.load(response)
assert restored['messages'][-1]['evaluation_cases']['narrative'] == answer['narrative']
record = {'model': model, 'question': question, 'events': events, 'new_approved_item_cited': reference,
          'raw_measurement_rows_sent': False, 'restored_identically': True}
output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'model': model, 'conversation_id': events['conversation']['id'], 'new_approved_item_cited': reference,
    'findings': [s['text'] for s in answer['narrative']['findings']], 'next_checks': [s['text'] for s in answer['narrative']['next_checks']],
    'caveats': [s['text'] for s in answer['narrative']['caveats']], 'restored_identically': True}, ensure_ascii=False), flush=True)
