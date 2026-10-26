"""Pivot controls -> saved SQL -> versions, table and dashboard; no source writes."""
from copy import deepcopy
import uuid

from sqlalchemy import delete, event

from app.db import SessionLocal, source_engine
from app.models import DatasetVersion
from app.query import source_connection
from test_raw_sql_api import api


def recipe():
    return dict(source_id='inline_measurements', pivot=dict(
        keys=['lot_code', 'wafer_id'], operation_column='metric_name', value_column='value',
        time_column='measured_at', tie_breaker='id', require_all=True,
        outputs=[{'value':'resistance','name':'value_a'}, {'value':'CD','name':'value_b'}],
        filters=[{'column':'wafer_id','op':'lte','value':10}]))


def expected():
    with source_connection() as connection:
        assert connection.exec_driver_sql('SHOW transaction_read_only').scalar() == 'on'
        return [dict(row) for row in connection.exec_driver_sql('''
            WITH latest AS (
                SELECT DISTINCT ON (lot_code, wafer_id, metric_name) lot_code, wafer_id, metric_name, value
                FROM public.inline_measurements
                WHERE wafer_id <= 10 AND metric_name IN ('resistance','CD')
                  AND measured_at IS NOT NULL AND lot_code IS NOT NULL AND wafer_id IS NOT NULL
                ORDER BY lot_code, wafer_id, metric_name, measured_at DESC, id DESC NULLS LAST
            )
            SELECT lot_code, wafer_id,
                max(value) FILTER (WHERE metric_name='resistance') AS value_a,
                max(value) FILTER (WHERE metric_name='CD') AS value_b
            FROM latest GROUP BY lot_code, wafer_id
            HAVING count(value) FILTER (WHERE metric_name='resistance') > 0
               AND count(value) FILTER (WHERE metric_name='CD') > 0
            ORDER BY lot_code, wafer_id
        ''').mappings()]


def test_operation_suggestions_and_preview_execute_generated_sql_exactly(api):
    suggestions = api.post('/api/sources/inline_measurements/pivot-values', json={'column':'metric_name'})
    assert suggestions.status_code == 200, suggestions.text
    assert {'resistance','CD'}.issubset(suggestions.json()['values'])
    assert api.post('/api/sources/inline_measurements/pivot-values', json={'column':'missing;DROP TABLE x'}).status_code == 422
    assert api.post('/api/sources/missing/pivot-values', json={'column':'metric_name'}).status_code == 404
    body = dict(spec=recipe(), editor_mode='simple', context={'limit':30})
    exported = api.post('/api/datasets/sql', json=body)
    assert exported.status_code == 200, exported.text
    sql = exported.json()['sql']
    assert 'LAST_VALUE(value)' in sql and 'UNBOUNDED FOLLOWING' in sql
    assert exported.json()['simple_editor']['available']
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(source_engine, 'before_cursor_execute', capture)
    try:
        response = api.post('/api/datasets/preview', json=body)
    finally:
        event.remove(source_engine, 'before_cursor_execute', capture)
    assert response.status_code == 200, response.text
    result = response.json()
    assert statements.count(sql) == 1
    assert result['rows'] == expected() and len(result['rows']) > 1
    assert result['spec']['raw_sql'] == result['canonical_sql'] == sql
    assert result['simple_editor']['available']
    restored = result['simple_editor']['spec']
    assert restored['pivot'] == result['spec']['pivot']
    assert api.post('/api/datasets/sql', json={'spec':restored,'editor_mode':'simple'}).json()['sql'] == sql


def test_saved_pivot_versions_feed_table_and_latest_scatter_and_keep_period_defaults(api):
    saved = api.post('/api/datasets', json={'name':'Pivot QA '+uuid.uuid4().hex[:8], 'project_id':1, 'spec':recipe()})
    assert saved.status_code == 201, saved.text
    dataset = saved.json()
    try:
        initial_sql = dataset['spec']['raw_sql']
        rows = api.post('/api/query', json={'dataset_id':dataset['id'],'dataset_version':1,'context':{'project_id':1}})
        assert rows.status_code == 200, rows.text
        assert rows.json()['rows'] == expected()
        assert [c['name'] for c in dataset['columns']] == ['lot_code','wafer_id','value_a','value_b']
        period = api.get(f"/api/datasets/{dataset['id']}/period-columns?project_id=1")
        assert period.status_code == 200 and period.json()['default']['column'] == 'measured_at'
        card = dict(id='pivot-preview',title='A vs B',dataset_id=dataset['id'],dataset_version=1,kind='scatter',x='value_a',y='value_b')
        response = api.post('/api/cards/query', json={'card':card,'context':{'project_id':1,'refresh':True}})
        assert response.status_code == 200, response.text
        pairs = lambda values: sorted((row['x'],row['y']) for row in values)
        assert pairs(response.json()['rows']) == sorted((row['value_a'],row['value_b']) for row in expected())
        # This far-future period must be applied inside the source before LAST_VALUE.
        empty = api.post('/api/cards/query', json={'card':card,'context':{'project_id':1,'period_start':{'year':2099,'week':1}}})
        assert empty.status_code == 200 and empty.json()['rows'] == []
        changed = recipe()
        changed['pivot']['outputs'][0]['name'] = 'x_measurement'
        update = api.post(f"/api/datasets/{dataset['id']}/versions", json={
            'name':dataset['name'],'project_id':1,'expected_version':1,'spec':changed})
        assert update.status_code == 201, update.text
        versions = api.get(f"/api/datasets/{dataset['id']}/versions?project_id=1").json()
        assert [item['version'] for item in versions] == [2,1]
        assert versions[1]['spec']['raw_sql'] == initial_sql
        for version in versions:
            reopened = api.post('/api/datasets/sql', json={'spec':version['spec'],'context':{'project_id':1}})
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()['simple_editor']['available']
            assert reopened.json()['sql'] == version['spec']['raw_sql']
        card['x'] = 'x_measurement'
        result = api.post('/api/cards/query', json={'card':card,'context':{'project_id':1}})
        assert result.status_code == 200, result.text
        assert pairs(result.json()['rows']) == pairs(response.json()['rows'])
        # Explicit history remains usable, and ownership still applies.
        historic = api.post('/api/query', json={'dataset_id':dataset['id'],'dataset_version':1,'context':{}})
        assert historic.status_code == 200 and historic.json()['rows'] == rows.json()['rows']
        assert api.post('/api/query', json={'dataset_id':dataset['id'],'dataset_version':1,'context':{'project_id':2}}).status_code == 422
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id == dataset['id']))


def test_edited_sql_detaches_pivot_and_stale_or_mixed_metadata_is_rejected(api):
    exported = api.post('/api/datasets/sql', json={'spec':recipe(),'editor_mode':'simple'}).json()
    original = exported['spec']
    edited = exported['sql'] + '\nLIMIT 1'
    state = api.post('/api/datasets/simple-editor', json={'source_id':original['source_id'],'sql':edited,'pivot':original['pivot']})
    assert state.status_code == 200 and state.json()['available'] is False
    stale = deepcopy(original)
    stale['raw_sql'] = edited
    assert api.post('/api/datasets/preview', json={'spec':stale}).status_code == 422
    direct = {'source_id':original['source_id'],'raw_sql':edited}
    result = api.post('/api/datasets/preview', json={'spec':direct})
    assert result.status_code == 200, result.text
    assert result.json()['canonical_sql'] == edited and len(result.json()['rows']) == 1
    assert not result.json()['simple_editor']['available']
    assert result.json()['spec']['pivot'] is None
    for field, value in [('columns',[{'name':'x','expression':'value'}]),('filters',[{'column':'value','op':'gt','value':0}]),('joins',['relationship'])]:
        mixed = recipe() | {field:value}
        assert api.post('/api/datasets/preview', json={'spec':mixed}).status_code == 422
