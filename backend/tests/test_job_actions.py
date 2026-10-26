"""Actions exercise real routes and workers with isolated metadata and bounded fakes."""
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from app import activity, db as database, job_actions
from app.analytics import service, worker
from app.analytics.models import AnalysisRun, AnalysisProposal
from app.analytics.data import source_snapshot
from app.models import User, utcnow
from app.semantic.collector_models import CollectionSettings, KnowledgeQuestion, CollectionDatasetScan
from app.semantic import collector_service, collector_config, collector_worker
from app.semantic.collector_schemas import GeneratedQuestions
from test_analytics_service import env, submit, trend_request, measurement


@pytest.fixture(autouse=True)
def actions(env):
    env.client.app.include_router(activity.router)


def act(env, identity, action='cancel', revision=1, kind='analysis_run', **extra):
    body = {'target': {'kind': kind, 'id': identity}, 'action': action,
            'expected_revision': revision, 'workspace_token': database.workspace_token(), **extra}
    return env.client.post('/api/workspace/activity/actions', json=body)


def detail(env, identity):
    return env.client.get('/api/analytics/runs/' + identity).json()


def test_queued_cancel_retry_is_linked_and_idempotent(env, monkeypatch):
    original = submit(env, trend_request())
    assert original['controls']['can_cancel']
    cancelled = act(env, original['id'])
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['status'] == 'cancelled'
    assert act(env, original['id']).status_code == 200
    assert worker.claim() is None
    revision = detail(env, original['id'])['revision']
    retried = act(env, original['id'], 'retry', revision)
    assert retried.status_code == 200, retried.text
    child = retried.json()['target']['id']
    assert child != original['id']
    assert act(env, original['id'], 'retry', revision).json()['target']['id'] == child
    monkeypatch.setattr(service, 'extract', lambda *a: ([measurement()], [], {}))
    assert worker.tick()
    assert detail(env, child)['status'] == 'completed'
    assert detail(env, child)['retry_of'] == original['id']
    parent = detail(env, original['id'])
    assert parent['status'] == 'cancelled' and parent['result'] is None
    assert parent['request'] == detail(env, child)['request']
    assert parent['controls']['retry_run_id'] == child and not parent['controls']['can_retry']
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 2


@pytest.mark.parametrize('late_error', [False, True])
def test_cancel_during_execution_discards_late_result_or_error(env, monkeypatch, late_error):
    original = submit(env, trend_request(min_samples=None, absolute_delta=None))
    assert worker.claim()[0] == original['id']
    before = detail(env, original['id'])
    def extract(*args):
        response = act(env, original['id'], revision=before['revision'])
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'cancelling'
        assert worker.claim() is None
        if late_error:
            raise RuntimeError('private source failure')
        return [measurement()], [], {'captured_at': 'late'}
    monkeypatch.setattr(service, 'extract', extract)
    service.process_run(original['id'])
    after = detail(env, original['id'])
    assert after['status'] == 'cancelled'
    assert after['result'] is None and not after['source_watermark'] and not after['error']
    assert after['questions'] == before['questions']


def test_cancel_between_claim_and_execution_acknowledged_without_extract(env, monkeypatch):
    original = submit(env, trend_request())
    worker.claim()
    assert act(env, original['id'], revision=2).json()['status'] == 'cancelling'
    extract = Mock(side_effect=AssertionError('cancelled work ran'))
    monkeypatch.setattr(service, 'extract', extract)
    service.process_run(original['id'])
    assert detail(env, original['id'])['status'] == 'cancelled'
    extract.assert_not_called()


def test_expired_cancellation_releases_queue_after_restart(env):
    first, second = submit(env, trend_request()), submit(env, trend_request())
    worker.claim()
    act(env, first['id'], revision=2)
    with env.sessions.begin() as db:
        db.get(AnalysisRun, first['id']).lease_until = utcnow() - timedelta(seconds=1)
    assert worker.claim()[0] == second['id']
    assert detail(env, first['id'])['status'] == 'cancelled'


@pytest.mark.parametrize('status', ['completed', 'needs_input', 'failed'])
def test_finished_and_input_work_cannot_be_cancelled(env, status):
    original = submit(env, trend_request())
    with env.sessions.begin() as db:
        db.get(AnalysisRun, original['id']).status = status
    assert act(env, original['id']).status_code == 409


def test_stale_workspace_revision_and_foreign_scope_rejected(env):
    original = submit(env, trend_request())
    assert act(env, original['id'], workspace_token='old').status_code == 409
    worker.claim()
    assert act(env, original['id']).status_code == 409
    assert act(env, original['id'], revision=2, target={'kind':'analysis_run','id':original['id'],'project_id':7}).status_code == 409
    with database.use_source(database.SourceBinding(None, 'b')):
        assert act(env, original['id'], revision=2).status_code == 404
    assert detail(env, original['id'])['status'] == 'running'


@pytest.mark.parametrize('role,active,owner', [('viewer',True,'admin'),('editor',True,'other'),('admin',False,'admin')])
def test_actions_recheck_current_account_and_ownership(env, role, active, owner):
    original = submit(env, trend_request())
    with env.sessions.begin() as db:
        actor = db.get(User, 'admin');actor.role, actor.active = role, active
        db.get(AnalysisRun, original['id']).created_by = owner
    assert act(env, original['id']).status_code == 403
    assert detail(env, original['id'])['status'] == 'queued'


def test_retry_revalidates_snapshot_and_rolls_back_child(env):
    original = submit(env, trend_request())
    act(env, original['id'])
    from app.models import Source
    with env.sessions.begin() as db:
        db.get(Source, 'a-epm').table_name = 'changed_source'
    assert act(env, original['id'], 'retry', 2).status_code in {404, 409}
    with env.sessions() as db:
        assert db.get(AnalysisRun, original['id']).retry_run_id is None
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1


def test_proposal_cancel_cannot_confirm_and_creates_no_analysis_questions(env):
    with env.sessions.begin() as db:
        db.add(AnalysisProposal(id='proposal', owner_id='admin', owner_role='admin', connection_key='a',
            workspace_token=database.workspace_token(), request=service.AnalysisRequest.model_validate(trend_request()).model_dump(mode='json'), source_snapshot=source_snapshot(db)[0], context={}))
    response = act(env, 'proposal', kind='analysis_proposal')
    assert response.status_code == 200, response.text
    restored = env.client.get('/api/analytics/proposals/proposal').json()
    assert restored['status'] == 'cancelled'
    confirmed = env.client.post('/api/analytics/proposals/proposal/confirm', json={
        'expected_revision': restored['revision'], 'workspace_token': database.workspace_token(),
        'project_id': None, 'request': trend_request()})
    assert confirmed.status_code == 409, confirmed.text
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0


@pytest.mark.parametrize('late_error', [False, True])
def test_collection_cancel_preserves_schedule_and_discards_late_questions(env, monkeypatch, late_error):
    monkeypatch.setattr(collector_config, 'configured', lambda: True)
    def complete(*args):
        response = act(env, None, revision=2, kind='knowledge_collection')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'cancelling'
        if late_error:
            raise RuntimeError('private provider error')
        return GeneratedQuestions(questions=[])
    monkeypatch.setattr(collector_config, 'complete', complete)
    with env.sessions.begin() as db:
        cfg = collector_service.ensure_settings(db)
        cfg.enabled, cfg.owner_id = True, 'admin'
    result = collector_service.generate_questions(actor_id='admin')
    assert result['status'] == 'cancelled', result
    with env.sessions() as db:
        cfg = db.get(CollectionSettings, 'a')
        assert cfg.enabled and cfg.next_run and not cfg.lease_token and cfg.execution_revision == 4
        assert db.scalar(select(func.count()).select_from(CollectionDatasetScan)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion)) == 0
    assert act(env, None, 'retry', 4, kind='knowledge_collection').status_code == 429
    with env.sessions.begin() as db:
        db.get(CollectionSettings, 'a').last_run = utcnow() - timedelta(minutes=2)
    monkeypatch.setattr(collector_config, 'complete', lambda *args: GeneratedQuestions())
    retry = act(env, None, 'retry', 4, kind='knowledge_collection')
    assert retry.status_code == 200, retry.text
    assert act(env, None, 'retry', 4, kind='knowledge_collection').status_code == 409
    with env.sessions() as db:
        assert db.get(CollectionSettings, 'a').last_status == 'completed'


def test_collection_restart_finishes_cancelled_lease_even_when_schedule_disabled(env):
    with env.sessions.begin() as db:
        cfg = collector_service.ensure_settings(db)
        cfg.last_status, cfg.lease_until, cfg.lease_token = 'cancelling', utcnow()-timedelta(seconds=1), 'old'
    collector_worker.tick()
    with env.sessions() as db:
        cfg = db.get(CollectionSettings, 'a')
        assert cfg.last_status == 'cancelled' and cfg.lease_token is None


def test_retry_keeps_question_answer_approval_and_resumes_with_explicit_criteria(env, monkeypatch):
    from app.semantic.collector_schemas import ExtractedKnowledge
    original = submit(env, trend_request(min_samples=None, absolute_delta=None))
    question = original['questions'][0]
    act(env, original['id'])
    child_id = act(env, original['id'], 'retry', 2).json()['target']['id']
    assert detail(env, child_id)['questions'] == original['questions']
    def extract(prompt, payload, schema):
        answer = payload['answers'][-1]
        return ExtractedKnowledge(name='재시도 분석 기준', fields={'description': {
            'value': answer['text'], 'quote': answer['text'], 'answer_id': answer['id']}})
    monkeypatch.setattr(collector_config, 'configured', lambda: True)
    monkeypatch.setattr(collector_config, 'complete', extract)
    qpath = '/api/semantic/collection/questions/' + question['id']
    q = env.client.get(qpath).json()
    answer = env.client.post(qpath+'/answers', json={'expected_revision':q['revision'],
        'text':'최소 유효 표본 1개, 평균 변화량 2를 사용합니다.'})
    assert answer.status_code == 202, answer.text
    draft = env.client.get('/api/semantic/collection/drafts').json()[0]
    approval = env.client.post('/api/semantic/collection/drafts/'+draft['id']+'/review',json={
        'expected_revision':draft['revision'],'decision':'approve','note':'재시도 기준 확인'})
    assert approval.status_code == 200, approval.text
    parent, child = detail(env, original['id']), detail(env, child_id)
    definition = child['questions'][0]['approved_definition']
    assert parent['questions'][0]['approved_definition'] == definition
    assert parent['request']['min_samples'] is None and child['request']['min_samples'] is None
    resumed = submit(env, trend_request(knowledge_refs=[{'item_id':definition['item_id'],'version':definition['version']}]))
    monkeypatch.setattr(service, 'extract', lambda *a: ([measurement(),measurement(20,at='2026-10-04T00:00:00Z',record_id=2)], [], {}))
    while worker.tick():
        pass
    assert detail(env, resumed['id'])['status'] == 'completed'
    assert detail(env, resumed['id'])['result']['series'][0]['status'] == 'changed'
    assert detail(env, original['id'])['status'] == 'cancelled'
    assert env.client.get(qpath).json()['draft']['status'] == 'approved'


def test_chat_history_refreshes_cancelling_to_cancelled_without_inventing_missing_conditions(env):
    from app.analytics.chat_tools import restore_answer
    original = submit(env, trend_request())
    worker.claim()
    act(env, original['id'], revision=2)
    response = {'run_id':original['id'],'status':'running','explanation':'old processing message'}
    cancelling = restore_answer(response)
    assert cancelling['status'] == 'cancelling' and '취소 요청' in cancelling['explanation']
    service.process_run(original['id'])
    cancelled = restore_answer(cancelling)
    assert cancelled['status'] == 'cancelled' and '취소되었습니다' in cancelled['explanation']
    assert '조건이 정해지지 않아' not in cancelled['explanation']
