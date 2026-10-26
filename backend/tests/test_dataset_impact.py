"""Impact previews run source SELECTs, but never save metadata or execute cards."""
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, func, select

from app import db as database, main, query
from app.auth import get_current_user
from app.db import SessionLocal, source_database_key
from app.dataset_impact_contract import DatasetImpact
from app.models import Audit, Dashboard, DatasetVersion, ProjectRemoval, Source
from test_project_datasets import api


SQL = 'SELECT id, equipment_id, value, measured_at FROM public.inline_measurements LIMIT 20'


@pytest.fixture
def records(api):
    datasets, boards, sources, removals = [], [], [], []

    def dataset(project_id=None, sql=SQL, source_id='inline_measurements'):
        result = api.post('/api/datasets', json={'name': 'Impact '+uuid.uuid4().hex[:8],
            'project_id': project_id, 'spec': {'source_id': source_id, 'raw_sql': sql}})
        assert result.status_code == 201, result.text
        item = result.json()
        datasets.append(item['id'])
        return item

    def board(cards, project_id=None, connection_key=None):
        identity = 'impact_'+uuid.uuid4().hex
        with SessionLocal.begin() as db:
            db.add(Dashboard(id=identity, version=3, project_id=project_id,
                connection_key=connection_key or source_database_key(), cards=cards,
                layout=[{'i': c['id'], 'x': 0, 'y': i*3, 'w': 4, 'h': 3} for i, c in enumerate(cards)]))
        boards.append(identity)
        return identity

    def source(identity, table, columns):
        with SessionLocal.begin() as db:
            db.add(Source(id=identity, name=identity, connection_key=source_database_key(),
                schema_name='public', table_name=table, primary_key='', columns=columns))
        sources.append(identity)

    def removed_project():
        identity = 9000000 + int(uuid.uuid4().hex[:5], 16)
        with SessionLocal.begin() as db:
            db.add(ProjectRemoval(id=identity, connection_key=source_database_key()))
        removals.append(identity)
        return identity

    yield SimpleNamespace(dataset=dataset, board=board, source=source, removed_project=removed_project)
    with SessionLocal.begin() as db:
        db.execute(delete(Dashboard).where(Dashboard.id.in_(boards)))
        db.execute(delete(DatasetVersion).where(DatasetVersion.id.in_(datasets)))
        db.execute(delete(Source).where(Source.id.in_(sources)))
        db.execute(delete(ProjectRemoval).where(ProjectRemoval.id.in_(removals)))
        db.execute(delete(Audit).where(Audit.entity_id.in_(datasets)))


def card(dataset, **changes):
    return dict(id='card-'+uuid.uuid4().hex[:8], title='Referenced chart', dataset_id=dataset['id'],
                dataset_version=1, kind='bar', x='equipment_id', y='value', aggregate='avg') | changes


def impact(api, dataset, *, spec=None, **changes):
    return api.post(f"/api/datasets/{dataset['id']}/impact", json=dict(
        name=dataset['name'], spec=spec or dataset['spec'], project_id=dataset['project_id'],
        expected_version=dataset['version']) | changes)


def spec(sql, source_id='inline_measurements'):
    return {'source_id': source_id, 'raw_sql': sql}


def test_common_impact_lists_current_boards_and_preserves_all_state(api, records, monkeypatch):
    dataset = records.dataset()
    ids = {records.board([card(dataset)], project_id=p) for p in (None, 1, 2)}
    records.board([card(dataset)], connection_key='foreign-workspace')
    records.board([card(dataset)], project_id=records.removed_project())
    with SessionLocal() as db:
        before = {table.__tablename__: list(db.execute(select(table.__table__)).mappings())
                  for table in (DatasetVersion, Dashboard, Audit)}
    generation = query._chart_cache._generation
    monkeypatch.setattr(query, 'clear_cache', lambda: pytest.fail('Impact must not clear cache'))
    result = impact(api, dataset, spec=spec(SQL.replace('value,', 'value, value * 2 AS added_value,')))
    assert result.status_code == 200, result.text
    response = DatasetImpact.model_validate_json(result.content)
    assert response.current_version == 1 and response.proposed_version == 2
    assert response.validation_mode == 'static'
    assert {c.dashboard_id for c in response.cards} == ids
    assert all(c.dashboard_version == 3 and c.status == 'compatible' for c in response.cards)
    assert all(c.project_name for c in response.cards if c.project_id is not None)
    assert response.column_changes[0].model_dump() == {
        'name': 'added_value', 'change': 'added', 'previous_type': None, 'next_type': 'double precision'}
    assert query._chart_cache._generation == generation
    with SessionLocal() as db:
        after = {table.__tablename__: list(db.execute(select(table.__table__)).mappings())
                 for table in (DatasetVersion, Dashboard, Audit)}
    assert after == before


def test_project_ownership_stale_version_and_editor_permissions(api, records):
    dataset = records.dataset(project_id=1)
    wanted = records.board([card(dataset)], project_id=1)
    records.board([card(dataset)], project_id=2)
    records.board([card(dataset)])
    response = impact(api, dataset)
    assert response.status_code == 200, response.text
    assert [c['dashboard_id'] for c in response.json()['cards']] == [wanted]
    assert impact(api, dataset, expected_version=2).status_code == 409
    assert impact(api, dataset, project_id=None).status_code == 422
    assert impact(api, dataset, project_id=2).status_code == 422
    assert impact(api, dataset, project_id=records.removed_project()).status_code == 404
    assert impact(api, dataset | {'id': 'missing'}).status_code == 404
    main.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='reader', role='viewer', active=True)
    try:
        assert impact(api, dataset).status_code == 403
    finally:
        main.app.dependency_overrides.pop(get_current_user)


def test_hidden_and_foreign_workspace_datasets_are_unavailable(api, records, monkeypatch):
    dataset = records.dataset()
    original = database.current_source()
    for binding in (database.SourceBinding(original.engine, original.key, ignored_tables=(('public', 'inline_measurements'),)),
                    database.SourceBinding(original.engine, 'other-db', ignored_tables=())):
        monkeypatch.setattr(main, 'load_active_source', lambda: binding)
        assert impact(api, dataset).status_code == 404


def test_invalid_candidate_cannot_be_reported_as_compatible(api, records):
    dataset = records.dataset()
    assert impact(api, dataset, spec=spec('SELECT missing_column FROM public.inline_measurements')).status_code == 409
    assert impact(api, dataset, spec=spec('DELETE FROM public.inline_measurements')).status_code == 422


def test_removed_columns_and_existing_errors_have_different_explanations(api, records):
    dataset = records.dataset()
    affected = card(dataset)
    existing = card(dataset) | {'y': 'already_missing'}
    filtered = card(dataset) | {'y': None, 'aggregate': 'count', 'filters': [{'column': 'value', 'op': 'gt', 'value': 1}]}
    records.board([affected, existing, filtered])
    result = impact(api, dataset, spec=spec('SELECT id, equipment_id, measured_at FROM public.inline_measurements LIMIT 20'))
    assert result.status_code == 200, result.text
    response = result.json()
    assert response['column_changes'] == [{'name': 'value', 'change': 'removed', 'previous_type': 'double precision', 'next_type': None}]
    impacts = {c['card_id']: c for c in response['cards']}
    assert impacts[affected['id']]['status'] == impacts[filtered['id']]['status'] == 'incompatible'
    assert impacts[existing['id']]['status'] == 'review'
    assert any('기존 버전에서도' in issue for issue in impacts[existing['id']]['issues'])


def test_type_changes_check_axes_expressions_and_python_without_execution(api, records, monkeypatch):
    dataset = records.dataset()
    axis = card(dataset) | {'kind': 'line', 'x': 'value', 'y': None, 'aggregate': 'count', 'options': {'x_scale': 'log'}}
    expression = card(dataset) | {'kind': 'kpi', 'x': None, 'y': None, 'aggregate': 'sum',
        'value_expression': [{'kind': 'column', 'name': 'value'}]}
    python = card(dataset) | {'editor_mode': 'python', 'python_script': 'fig = go.Figure()', 'script_input': 'dataset'}
    records.board([axis, expression, python])
    monkeypatch.setattr('app.plotly_scripts.execute_script', lambda *a, **k: pytest.fail('Impact executed a script'))
    result = impact(api, dataset, spec=spec(SQL.replace('value,', 'value::text AS value,')))
    assert result.status_code == 200, result.text
    impacts = {c['card_id']: c for c in result.json()['cards']}
    assert impacts[axis['id']]['status'] == impacts[expression['id']]['status'] == 'incompatible'
    assert impacts[python['id']]['status'] == 'review'
    assert any('실행하지 않았습니다' in issue for issue in impacts[python['id']]['issues'])
    assert result.json()['column_changes'][0]['change'] == 'type_changed'


def test_period_basis_and_yield_required_columns_are_checked(api, records):
    dataset = records.dataset(sql='SELECT id, equipment_id, value AS tested_dies, value AS passed_dies, measured_at FROM public.inline_measurements LIMIT 20')
    yield_card = card(dataset) | {'kind': 'kpi', 'x': None, 'y': None, 'aggregate': 'yield'}
    records.board([yield_card])
    result = impact(api, dataset, spec=spec('SELECT id, equipment_id, value AS tested_dies, measured_at FROM public.inline_measurements LIMIT 20'))
    assert result.status_code == 200, result.text
    assert result.json()['cards'][0]['status'] == 'incompatible'
    dated = records.dataset()
    records.board([card(dated) | {'period': {'source_id': 'inline_measurements', 'column': 'measured_at', 'use_dashboard': True}}])
    result = impact(api, dated, spec=spec('SELECT id, id AS equipment_id, 0::double precision AS value FROM public.equipment', 'equipment'))
    assert result.status_code == 200, result.text
    assert result.json()['cards'][0]['status'] == 'incompatible'
    assert any('공통 기간' in issue for issue in result.json()['cards'][0]['issues'])


def test_gantt_table_mode_tracks_display_dataset_not_auxiliary_dataset_ids(api, records, monkeypatch):
    prefix = 'impact_'+uuid.uuid4().hex[:12]
    plan = prefix+'_plan'
    records.source(plan, 'inline_measurements', [{'name': n, 'type': t} for n, t in
        [('lot_cd', 'text'), ('lot_id', 'text'), ('operation_seq', 'integer'), ('operation', 'text')]])
    for role, extra in [('status', [('remark', 'text')]), ('tat', [('tat', 'numeric')]), ('history', [('end_dt_tm', 'timestamp')])]:
        records.source(prefix+'_'+role, prefix+'_'+role, [{'name': n, 'type': t} for n, t in
            [('lot_cd', 'text'), ('lot_id', 'text'), ('operation', 'text'), *extra]])
    plan_sql = 'SELECT lot_code AS lot_cd, lot_id::text AS lot_id, 1 AS operation_seq, metric_name AS operation FROM public.inline_measurements LIMIT 20'
    dataset = records.dataset(sql=plan_sql, source_id=plan)
    gantt = card(dataset) | {'kind': 'gantt', 'x': None, 'y': None, 'aggregate': 'none', 'gantt': {
        'data_mode': 'tables', **{role+'_source_id': prefix+'_'+role for role in ('status', 'tat', 'history')}}}
    records.board([gantt])
    # These IDs denote physical tables; they do not make another dataset a dependency.
    auxiliary_only = dict(gantt, id='auxiliary-only', dataset_id='another-dataset',
                          gantt=dict(gantt['gantt'], status_source_id=dataset['id']))
    records.board([auxiliary_only])
    result = impact(api, dataset, spec=spec(plan_sql.replace('1 AS operation_seq', '2 AS operation_seq'), plan))
    assert result.status_code == 200, result.text
    assert [c['card_id'] for c in result.json()['cards']] == [gantt['id']]
    assert result.json()['cards'][0]['status'] == 'compatible'
    removed = impact(api, dataset, spec=spec(plan_sql.replace(', 1 AS operation_seq', ''), plan))
    assert removed.json()['cards'][0]['status'] == 'incompatible'
    original = database.current_source()
    monkeypatch.setattr(main, 'load_active_source', lambda: database.SourceBinding(original.engine, original.key,
        ignored_tables=(('public', prefix+'_status'),)))
    assert impact(api, dataset).json()['cards'] == []


def test_openapi_exposes_the_authoritative_response_model(api):
    schema = api.get('/openapi.json').json()
    endpoint = schema['paths']['/api/datasets/{dataset_id}/impact']['post']
    assert endpoint['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/DatasetImpact')
    assert endpoint['requestBody']['content']['application/json']['schema']['$ref'].endswith('/DatasetUpdate')
    assert set(schema['components']['schemas']['DatasetImpact']['properties']) == set(DatasetImpact.model_fields)
