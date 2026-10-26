"""Activity is a read-only summary of isolated metadata, without measurement access."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import activity, catalog, db as database, project_settings
from app.activity_contract import WorkspaceActivity
from app.analytics import service
from app.analytics.data import REQUIRED, TABLES, source_snapshot
from app.analytics.models import AnalysisProposal, AnalysisRun, migrate_analytics
from app.analytics.schemas import AnalysisRequest
from app.auth import get_current_user
from app.models import AppProject, Audit, DatabaseProfile, DatasetVersion, ProjectRemoval, Relation, Source, User, utcnow
from app.semantic.collector_models import CollectionSettings
from app.semantic.migration import migrate_semantic


BASE = '/api/workspace/activity'
AT = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)


class NoSourceAccess:
    def connect(self, *args, **kwargs):
        raise AssertionError('Activity must not scan measurement sources.')


@pytest.fixture
def env(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    for model in (User, Source, Relation, DatasetVersion, Audit, DatabaseProfile, AppProject, ProjectRemoval):
        model.__table__.create(engine)
    migrate_semantic(engine)
    migrate_analytics(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (database, catalog, project_settings):
        monkeypatch.setattr(module, 'SessionLocal', sessions)
    binding = database.SourceBinding(NoSourceAccess(), 'activity-db', revision=3, ignored_tables=())
    monkeypatch.setattr(database, 'active_source', binding)
    with sessions.begin() as db:
        db.add(User(id='me', username='me', role='admin', name='관리자'))
        for family, table in TABLES.items():
            columns = [{'name': c, 'type': 'text'} for c in REQUIRED[family]]
            db.add(Source(id=family, connection_key=binding.key, name=table, schema_name='public',
                          table_name=table, primary_key='record_id', columns=columns))
            db.add(DatasetVersion(id=family + '-data', version=1, name=table,
                                 columns=columns, spec={'source_id': family, 'joins': []}))
        for identity in (1, 2):
            db.add(AppProject(id=identity, connection_key=binding.key, name=f'프로젝트 {identity}', lot_cds=[f'LOT{identity}']))
    actor = SimpleNamespace(id='me', role='admin', active=True)
    app = FastAPI()
    app.include_router(activity.router)
    app.dependency_overrides[get_current_user] = lambda: actor

    def run(identity, *, project_id=None, **changes):
        with sessions.begin() as db:
            request = {'kind': 'statistics', 'family': 'epm', 'parameter': 'PARA1', 'project_id': project_id}
            if project_id is not None and db.get(AppProject, project_id):
                request['_project_scope'] = service.project_scope(db, project_id)
            values = dict(id=identity, connection_key=binding.key, kind='statistics', status='completed',
                request=request, source_snapshot={'epm': source_snapshot(db)[0]['epm']},
                result={'large': 'private result' * 10000}, created_by='other-user', created_at=AT,
                started_at=AT + timedelta(minutes=1), completed_at=AT + timedelta(minutes=2))
            db.add(AnalysisRun(**(values | changes)))

    def proposal(identity, *, project_id=None, **changes):
        with sessions.begin() as db:
            values = dict(id=identity, owner_id='me', owner_role=actor.role, connection_key=binding.key,
                workspace_token=database.workspace_token(), project_id=project_id,
                project_scope=service.project_scope(db, project_id), status='pending',
                request={'kind': 'trend', 'family': 'epm', 'project_id': project_id},
                source_snapshot=source_snapshot(db)[0], context={'private_prompt': 'not returned'}, created_at=AT)
            db.add(AnalysisProposal(**(values | changes)))

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, engine=engine, actor=actor,
                              binding=binding, run=run, proposal=proposal, app=app)
    engine.dispose()


def items(env, query=''):
    response = env.client.get(BASE + query)
    assert response.status_code == 200, response.text
    WorkspaceActivity.model_validate_json(response.content)
    return response.json()['items']


def test_serialized_summary_has_honest_timestamps_direct_targets_and_no_writes(env):
    env.run('done', source_watermark={'captured_at': '2026-10-20T02:01:00Z', 'sources': {
        'epm': {'source_id': 'epm', 'last_measured_at': '2026-10-18T11:00:00+09:00'},
        'history': {'source_id': 'history', 'last_measured_at': '2026-10-25T02:00:00Z'}}})
    env.run('working', status='running', completed_at=None, lease_until=utcnow() + timedelta(minutes=10))
    env.run('waiting', status='queued', started_at=None, completed_at=None)
    env.run('criteria', status='needs_input', started_at=None, completed_at=None)
    env.run('failed', status='failed', error='private provider error')
    env.proposal('review')
    statements = []
    capture = lambda conn, cursor, statement, params, context, many: statements.append(statement)
    event.listen(env.engine, 'before_cursor_execute', capture)
    try:
        response = env.client.get(BASE)
    finally:
        event.remove(env.engine, 'before_cursor_execute', capture)
    data = response.json()
    WorkspaceActivity.model_validate(data)
    assert data['workspace_token'] == 'activity-db:3' and data['timestamp_basis'] == 'stored_execution_records'
    assert datetime.fromisoformat(data['generated_at'].replace('Z', '+00:00')) > AT
    assert [r['status'] for r in data['items']] == ['running', 'queued', 'needs_confirmation', 'needs_input', 'failed', 'completed']
    done = next(r for r in data['items'] if r['id'] == 'analysis:done')
    assert done['target'] == {'kind': 'analysis_run', 'id': 'done', 'project_id': None}
    assert done['freshness'] == {'state': 'recorded', 'queried_at': '2026-10-20T02:01:00Z',
                                 'last_measured_at': '2026-10-18T11:00:00+09:00'}
    assert done['activity_at'] == '2026-10-20T02:02:00Z'
    assert 'private' not in response.text
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements)
    run_select = next(s for s in statements if 'FROM app_analysis_runs' in s)
    proposal_select = next(s for s in statements if 'FROM app_analysis_proposals' in s)
    assert 'app_analysis_runs.result' not in run_select and 'app_analysis_runs.error' not in run_select
    assert 'app_analysis_proposals.context' not in proposal_select
    schema = env.app.openapi()
    assert schema['paths'][BASE]['get']['responses']['200']['content']['application/json']['schema']['$ref'].endswith('WorkspaceActivity')


def test_all_projects_and_exact_project_filter_preserve_legacy_projects(env, monkeypatch):
    env.run('common')
    env.run('one', project_id=1)
    env.run('two', project_id=2)
    env.run('legacy', project_id=8)
    original = activity.projects_list
    monkeypatch.setattr(activity, 'projects_list', lambda: original() + [{'id': 8, 'name': '이전 프로젝트'}])
    assert {r['id'] for r in items(env)} == {'analysis:common', 'analysis:one', 'analysis:two', 'analysis:legacy'}
    assert [r['id'] for r in items(env, '?project_id=1')] == ['analysis:one']
    legacy = items(env, '?project_id=8')
    assert len(legacy) == 1 and legacy[0]['project_name'] == '이전 프로젝트'
    assert env.client.get(BASE + '?project_id=999').status_code == 404


@pytest.mark.parametrize('change', ['foreign_db', 'hidden', 'unavailable', 'schema', 'removed_project', 'inactive_project', 'changed_scope'])
def test_unavailable_work_is_excluded_without_leaking_titles(env, monkeypatch, change):
    env.run('sensitive', project_id=1)
    env.proposal('sensitive', project_id=1)
    with env.sessions.begin() as db:
        if change == 'foreign_db':
            for model in (AnalysisRun, AnalysisProposal):
                db.get(model, 'sensitive').connection_key = 'other'
        elif change == 'hidden':
            monkeypatch.setattr(database, 'active_source', replace(env.binding, ignored_tables=('epm_data',)))
        elif change == 'unavailable':
            db.get(Source, 'epm').available = False
        elif change == 'schema':
            db.get(Source, 'epm').columns = []
        elif change == 'removed_project':
            db.delete(db.get(AppProject, 1))
            db.add(ProjectRemoval(id=1, connection_key=env.binding.key))
        elif change == 'inactive_project':
            db.get(AppProject, 1).active = False
        else:
            db.get(AppProject, 1).lot_cds = ['DIFFERENT']
    assert items(env) == []


def test_private_proposals_require_current_owner_role_workspace_and_full_snapshot(env):
    env.proposal('mine')
    env.proposal('someone-else', owner_id='other')
    env.proposal('old-role', owner_role='editor')
    env.proposal('old-workspace', workspace_token='activity-db:2')
    env.proposal('confirmed', status='confirmed')
    env.proposal('old-catalog', source_snapshot={})
    env.proposal('other-project', project_id=2)
    assert {r['id'] for r in items(env)} == {'proposal:mine', 'proposal:other-project'}
    assert [r['id'] for r in items(env, '?project_id=2')] == ['proposal:other-project']
    env.actor.role = 'viewer'
    assert items(env) == []
    env.actor.active = False
    assert env.client.get(BASE).status_code == 403


def test_questions_are_counted_once_from_existing_visible_context(env, monkeypatch):
    with env.sessions.begin() as db:
        actor = db.get(User, 'me')
        for _ in range(2):
            service.create_run(db, AnalysisRequest(kind='trend', family='epm', parameter='PARA1'), actor)
    spy = Mock(wraps=activity.question_context)
    monkeypatch.setattr(activity, 'question_context', spy)
    result = items(env)
    assert len(result) == 2 and all(r['question_count'] == 3 for r in result)
    assert spy.call_count == 1


def test_collection_is_admin_only_global_and_does_not_invent_completion_or_freshness(env):
    with env.sessions.begin() as db:
        db.add(CollectionSettings(connection_key=env.binding.key, last_status='completed', last_run=AT,
                                  last_message='private source or provider details'))
    item = items(env)[0]
    assert item['kind'] == 'knowledge_collection' and item['status'] == 'completed'
    assert item['started_at'] == item['activity_at'] == '2026-10-20T02:00:00Z'
    assert item['completed_at'] is None and item['freshness']['state'] == 'unknown'
    assert 'private' not in str(item) and items(env, '?project_id=1') == []
    env.actor.role = 'editor'
    assert items(env) == []


def test_expired_leases_are_unknown_without_recovery_or_mutation(env):
    env.run('expired', status='running', completed_at=None, lease_until=AT)
    env.run('missing-lease', status='running', completed_at=None, lease_until=None)
    with env.sessions.begin() as db:
        db.add(CollectionSettings(connection_key=env.binding.key, last_status='running', last_run=AT, lease_until=AT))
    assert all(r['status'] == 'unknown' and r['message'] for r in items(env))
    with env.sessions() as db:
        assert all(r.status == 'running' for r in db.scalars(select(AnalysisRun)))
        assert db.get(CollectionSettings, env.binding.key).last_status == 'running'


def test_unknown_watermarks_stay_unknown_and_project_query_failure_is_explicit(env, monkeypatch):
    env.run('malformed', source_watermark={'captured_at': 'invalid', 'sources': []})
    env.run('untrusted', source_watermark={'captured_at': '2026-10-20T00:00:00', 'sources': {
        'epm': {'source_id': 'wrong', 'last_measured_at': '2026-10-20T00:00:00Z'}}})
    assert all(r['freshness'] == {'state': 'unknown', 'queried_at': None, 'last_measured_at': None} for r in items(env))
    def fail():
        raise OperationalError('private statement', {}, RuntimeError('private details'))
    monkeypatch.setattr(activity, 'projects_list', fail)
    response = env.client.get(BASE)
    assert response.status_code == 503 and 'private' not in response.text


def test_bounded_list_reports_more_and_validates_limit(env):
    for i in range(3):
        env.run(str(i))
    response = env.client.get(BASE + '?limit=2').json()
    assert len(response['items']) == 2 and response['has_more'] is True
    assert env.client.get(BASE + '?limit=101').status_code == 422
    assert env.client.get(BASE + '?limit=0').status_code == 422


def test_old_running_job_is_not_displaced_by_newer_conditions_or_results(env):
    env.run('running', status='running', created_at=AT - timedelta(days=1),
            completed_at=None, lease_until=utcnow() + timedelta(minutes=10))
    with env.sessions.begin() as db:
        snapshot = {'epm': source_snapshot(db)[0]['epm']}
        for i in range(105):
            db.add(AnalysisRun(id=f'needs-{i}', connection_key=env.binding.key, kind='trend', status='needs_input',
                request={'kind': 'trend', 'project_id': None}, source_snapshot=snapshot, created_by='me', created_at=AT))
    response = env.client.get(BASE + '?limit=2').json()
    assert response['items'][0]['id'] == 'analysis:running' and response['has_more'] is True
