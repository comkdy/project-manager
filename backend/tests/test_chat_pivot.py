"""AI recipes use the same pivot compiler/editor/save path as manual recipes."""
from copy import deepcopy
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import delete

from app.chat_contracts import QueryDraft
from app.db import SessionLocal
from app.models import Audit, DatasetVersion
from test_chat_workflow import api, counts, definition, events, mock_llm, needs_dataset
from test_pivot_api import expected, recipe


def pivot_draft():
    return {"kind": "query", "explanation": "각 항목별 마지막 값", "name": "QA AI pivot",
            **recipe()}


def editor(api, spec):
    response = api.post('/api/datasets/sql', json={'spec': spec})
    assert response.status_code == 200, response.text
    return response.json()


def test_ai_pivot_preview_save_reopen_edit_and_version(api, monkeypatch):
    mock_llm(monkeypatch, [{"kind": "route", "intent": "dataset"}, pivot_draft(),
        {"kind": "dataset", "name": "QA AI pivot editable", "explanation": "설정과 SQL을 함께 저장"}])
    board = api.get('/api/dashboards').json()
    before = counts()
    p = events(api.post('/api/chat', json={'message': '마지막 값 피벗 데이터셋', 'project_id': 1}))['proposal']
    spec = p['dataset']['spec']
    assert spec['pivot'] == QueryDraft.model_validate(pivot_draft()).pivot.model_dump()
    assert p['query_result']['rows'] == expected()
    assert counts() == before
    generated = editor(api, spec)
    assert generated['simple_editor']['available']
    assert generated['sql'] == spec['raw_sql']
    body = {'proposal': definition(p), 'project_id': 1, 'workspace_token': p['workspace_token']}
    preview = api.post('/api/chat/preview', json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()['dataset']['spec'] == spec
    request_id = str(uuid.uuid4())
    created = None
    try:
        saved = api.post('/api/chat/apply', json={**body, 'request_id': request_id})
        assert saved.status_code == 200, saved.text
        created = saved.json()['dataset_id']
        reopened = api.get(f'/api/datasets/{created}/latest?project_id=1').json()
        assert reopened['spec'] == spec
        restored = editor(api, reopened['spec'])['simple_editor']['spec']
        restored['pivot']['outputs'][0]['name'] = 'last_resistance'
        restored['pivot']['filters'] = [{'column': 'wafer_id', 'op': 'eq', 'value': 1}]
        changed = api.post('/api/datasets/preview', json={'spec': restored, 'editor_mode': 'simple'})
        assert changed.status_code == 200, changed.text
        assert len(changed.json()['rows']) == 1
        assert changed.json()['rows'][0]['last_resistance'] == expected()[0]['value_a']
        version = api.post(f'/api/datasets/{created}/versions', json={
            'name': reopened['name'], 'project_id': 1, 'expected_version': 1, 'spec': changed.json()['spec']})
        assert version.status_code == 201, version.text
        versions = api.get(f'/api/datasets/{created}/versions?project_id=1').json()
        assert [v['version'] for v in versions] == [2, 1]
        assert versions[1]['spec'] == spec
        assert all(editor(api, v['spec'])['simple_editor']['available'] for v in versions)
    finally:
        if created:
            with SessionLocal.begin() as db:
                db.execute(delete(DatasetVersion).where(DatasetVersion.id == created))
                db.execute(delete(Audit).where(Audit.entity_id.in_([created, request_id])))
    assert counts() == before
    assert api.get('/api/dashboards').json() == board


def test_pivot_recipe_survives_card_dependency_and_preview(api, monkeypatch):
    card = {'id': 'new', 'title': 'Last A/B', 'dataset_id': 'draft-dataset', 'dataset_version': 1,
            'kind': 'scatter', 'x': 'value_a', 'y': 'value_b', 'aggregate': 'none'}
    calls = mock_llm(monkeypatch, [
        {'kind': 'route', 'intent': 'card'}, needs_dataset(api, '피벗 필요'),
        pivot_draft(), {'kind': 'dataset', 'name': 'AI 피벗', 'explanation': '마지막 값'},
        {'kind': 'card', 'explanation': '항목 비교', 'card': card}])
    result = events(api.post('/api/chat', json={'message': '마지막 값 산점도', 'project_id': 1}))
    assert 'error' not in result, result
    p = result['proposal']
    assert p['dataset']['spec']['pivot'] and p['card_result']['rows']
    assert '"pivot"' in calls[-1]['messages'][0]['content']
    preview = api.post('/api/chat/preview', json={
        'proposal': definition(p), 'project_id': 1, 'workspace_token': p['workspace_token']})
    assert preview.status_code == 200, preview.text
    assert preview.json()['dataset']['spec'] == p['dataset']['spec']
    assert preview.json()['card_result']['rows'] == p['card_result']['rows']


JOIN_SQL = """WITH ranked AS (
    SELECT w.wafer_code, m.metric_name, m.value,
        ROW_NUMBER() OVER (PARTITION BY w.wafer_code, m.metric_name ORDER BY m.measured_at DESC, m.id DESC) AS rn
    FROM public.inline_measurements m JOIN public.wafers w ON m.wafer_id = w.id
    WHERE w.project_id = 1 AND w.wafer_number IN (1,2)
)
SELECT wafer_code, MAX(value) FILTER (WHERE metric_name = 'CD') AS value_cd,
       MAX(value) FILTER (WHERE metric_name = 'resistance') AS value_resistance
FROM ranked WHERE rn = 1 GROUP BY wafer_code ORDER BY wafer_code"""


def test_join_pivot_keeps_exact_sql_and_sql_only_editor(api, monkeypatch):
    draft = {k: v for k, v in pivot_draft().items() if k != 'pivot'} | {'sql': JOIN_SQL}
    mock_llm(monkeypatch, [{'kind': 'route', 'intent': 'query'}, draft])
    p = events(api.post('/api/chat', json={'message': 'JOIN 후 피벗'}))['proposal']
    spec = p['dataset']['spec']
    assert spec['raw_sql'] == JOIN_SQL and spec['pivot'] is None
    assert p['query_result']['rows']
    assert not editor(api, spec)['simple_editor']['available']


def test_stale_pivot_metadata_cannot_be_previewed_or_saved(api):
    spec = editor(api, recipe())['spec']
    body = {'proposal': {'kind': 'dataset', 'dataset': {'name': 'stale QA', 'spec': spec}},
            'workspace_token': api.get('/api/bootstrap').json()['workspace_token']}
    before = counts()
    body['proposal']['dataset']['spec']['raw_sql'] = JOIN_SQL
    assert api.post('/api/chat/preview', json=body).status_code == 422
    assert api.post('/api/chat/apply', json={**body, 'request_id': str(uuid.uuid4())}).status_code == 422
    # A manual SQL edit deliberately drops metadata and remains exact SQL.
    body['proposal']['dataset']['spec']['pivot'] = None
    response = api.post('/api/chat/preview', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['dataset']['spec']['raw_sql'] == JOIN_SQL
    assert counts() == before


def test_bad_pivot_column_is_repaired_by_the_existing_bounded_retry(api, monkeypatch):
    bad = deepcopy(pivot_draft())
    bad['pivot']['value_column'] = 'not_a_column'
    calls = mock_llm(monkeypatch, [{'kind': 'route', 'intent': 'query'}, bad, pivot_draft()])
    result = events(api.post('/api/chat', json={'message': '피벗 조회'}))
    assert result['proposal']['dataset']['spec']['pivot']['value_column'] == 'value'
    assert len(calls) == 3 and 'validation feedback' in calls[-1]['messages'][-1]['content']


def test_pivot_where_cannot_hide_another_table(api):
    bad = recipe()
    bad['pivot']['where_sql'] = 'wafer_id IN (SELECT id FROM public.wafers)'
    assert api.post('/api/datasets/sql', json={'spec': bad}).status_code == 422


@pytest.mark.parametrize('sql,pivot', [(None, None), (JOIN_SQL, recipe()['pivot'])])
def test_model_must_choose_sql_or_pivot_without_two_sources_of_truth(sql, pivot):
    with pytest.raises(ValidationError, match='하나만'):
        QueryDraft.model_validate({**pivot_draft(), 'sql': sql, 'pivot': pivot})
