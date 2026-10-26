"""User-authorized review of one computed ML result; no model/source-row export."""
import json
import sys
import urllib.request
from pathlib import Path

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import db as database

RUN = '4591b8bf-fb52-45a0-8626-0e1fd4bd176f'
DRAFT = '32aafb13-397d-4fa4-af6c-92e787d98cad'
QUESTION = 'af58a484-72de-4b6a-876a-e07a175d0c53'
PROJECT = 1000004


def call(path, body=None):
    request = urllib.request.Request('http://127.0.0.1:8000/api' + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.load(response)


def snapshot():
    tables = json.loads((ROOT / 'validation/chat-worker-analysis-metadata-before.json').read_text()).keys()
    result = {}
    with database.engine.connect() as conn:
        conn.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        inspector = inspect(conn)
        for table in tables:
            keys = inspector.get_pk_constraint(table, schema='public')['constrained_columns']
            assert keys and table.replace('_', '').isalnum() and all(k.replace('_', '').isalnum() for k in keys)
            columns = ','.join('t."' + k + '"' for k in keys)
            values = conn.execute(text(f'SELECT jsonb_build_array({columns})::text, md5(to_jsonb(t)::text) FROM public."{table}" t')).all()
            result[table] = dict(values)
    return result


if __name__ == '__main__':
    baseline = ROOT / 'validation/chat-worker-approval-metadata-before.json'
    if not baseline.exists():
        baseline.write_text(json.dumps(snapshot(), indent=2), encoding='utf-8')
    draft = call('/semantic/collection/drafts/' + DRAFT)
    answer = call(f'/chat/data-analysis/{RUN}?project_id={PROJECT}')
    assert answer['status'] == 'completed' and answer['settings']['method'] == 'ml_regression'
    assert answer['dataset']['project_id'] == PROJECT and answer['dataset']['version'] == 2
    assert not draft.get('issues'), draft.get('issues')
    content = draft['proposal']['content']
    details = content['details']
    assert details['analysis_result'] == answer['result']['summary']
    conditions = json.loads(details['conditions'])
    assert conditions['dataset'] == answer['dataset'] and conditions['settings'] == answer['settings']
    assert conditions['captured_at'] == answer['result']['captured_at'] and conditions['filters'] == []
    assert RUN in details['evidence'] and '인과' in details['limitations']
    ml = answer['result']['ml']
    assert (answer['result']['row_count'], ml['validation']['train_rows'], ml['validation']['test_rows'], ml['validation']['purged_rows']) == (243, 183, 49, 11)
    assert ml['validation']['train_latest'] < ml['validation']['test_earliest']
    assert ml['metrics']['beats_baseline'] and ml['validation']['unit_columns'] == ['lot_id']
    if draft['status'] == 'pending':
        approval = call('/semantic/collection/drafts/' + DRAFT + '/review', {
            'expected_revision': draft['revision'], 'decision': 'approve',
            'note': '사용자 요청에 따라 검토·승인합니다. XXXA 결합 데이터셋 v2의 원본 실행과 수치·범위가 일치하고, 243행 중 과거 183행/미래 49행, Lot 중복 11행 제외를 확인했습니다. 합성 DB의 관측·예측 검증 결과로 축적하며 인과관계나 실제 사내 공정 효과로 확정하지 않습니다.'})
    else:
        assert draft['status'] == 'approved'
        approval = {k: draft[k] for k in ('status', 'item_id', 'item_version')}
    assert approval['status'] == 'approved' and approval['item_id'] and approval['item_version'] == 1
    question = call('/semantic/collection/questions/' + QUESTION)
    restored = call(f'/chat/data-analysis/{RUN}?project_id={PROJECT}')
    assert question['status'] == 'answered' and restored['review']['status'] == 'approved'
    assert restored['review']['item_id'] == approval['item_id'] and restored['result'] == answer['result']
    record = {'run_id': RUN, 'question_id': QUESTION, 'draft_id': DRAFT, 'project_id': PROJECT,
              'dataset': answer['dataset'], 'approval': approval, 'reviewed_summary': details['analysis_result'],
              'review': restored['review'], 'question_status': question['status'], 'raw_measurement_rows_sent': False}
    (ROOT / 'validation/chat-worker-result-approved.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in record.items() if k != 'reviewed_summary'}, ensure_ascii=False))
