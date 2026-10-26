"""Data-driven scheduling, immutable review and existing job lifecycle integration."""
from datetime import timedelta
from sqlalchemy import select
import pytest

from app import knowledge_watch as watches
from app.analytics import worker
from app.analytics.models import AnalysisRun, KnowledgeWatch
from app.models import DatasetVersion, User, utcnow
from app.semantic.collector_models import CollectedDraft, KnowledgeQuestion
from app.semantic.models import KnowledgeItem
from test_evaluation_analysis import setup, prepare, execute, fake_result
from test_semantic_collection import env, approve


@pytest.fixture
def ready(setup, monkeypatch):
    setup.client.app.include_router(watches.router)
    monkeypatch.setattr('app.evaluation_compute.compute', lambda c,p,s,cols,stored,check: fake_result(stored) | {'data_fingerprint':'initial'})
    run = prepare(setup)
    assert execute(setup,run).status_code == 202
    assert worker.tick()
    run = setup.client.get('/api/evaluation-analysis/'+run['id']).json()
    setup.run = run
    monkeypatch.setattr(watches, '_compile', lambda *a,**k:(None,'fixture',{},None))
    monkeypatch.setattr(watches, 'data_fingerprint', lambda *a:{'fingerprint':'initial','row_count':4})
    return setup


def create(env):
    response = env.client.post('/api/knowledge-watches',json={'run_id':env.run['id'],'expected_revision':env.run['revision'],
        'workspace_token':env.run['workspace_token'],'interval_minutes':15})
    assert response.status_code == 201, response.text
    return response.json()


def due(env, identity):
    with env.sessions.begin() as db:
        db.get(KnowledgeWatch,identity).next_check_at = utcnow()-timedelta(minutes=1)


def test_unchanged_skips_ml_changed_data_runs_once_and_approval_publishes(ready, monkeypatch):
    env=ready
    watch=create(env)
    assert create(env)['id']==watch['id']
    watches.tick() # initial result is reviewed through exactly the same path
    with env.sessions() as db:
        assert len(list(db.scalars(select(AnalysisRun))))==1
        assert len(list(db.scalars(select(KnowledgeQuestion).where(KnowledgeQuestion.subject.like('evaluation-run:%')))))==1
    due(env,watch['id']);watches.tick();watches.tick()
    with env.sessions() as db:
        assert len(list(db.scalars(select(AnalysisRun))))==1
    monkeypatch.setattr(watches,'data_fingerprint',lambda *a:{'fingerprint':'changed','row_count':5})
    due(env,watch['id']);watches.tick();watches.tick()
    with env.sessions() as db:
        row=db.get(KnowledgeWatch,watch['id']); identity=row.last_run_id
        assert identity!=env.run['id']
        assert len(list(db.scalars(select(AnalysisRun))))==2
        child=db.get(AnalysisRun,identity)
        assert child.status=='queued' and child.request['_knowledge_watch_change']['previous_fingerprint']=='initial'
    monkeypatch.setattr('app.evaluation_compute.compute',lambda c,p,s,cols,stored,check:fake_result(stored)|{'data_fingerprint':'changed'})
    assert worker.tick()
    watches.tick();watches.tick()
    item=env.client.get('/api/knowledge-watches').json()[0]
    q=env.client.get('/api/semantic/collection/questions/'+item['review']['question_id']).json()
    assert q['draft']['status']=='pending'
    assert approve(env,q['draft']).status_code==200
    with env.sessions() as db:
        assert len(list(db.scalars(select(KnowledgeItem))))==2 # original case + approved result
        assert len(list(db.scalars(select(AnalysisRun))))==2
    due(env,watch['id']);watches.tick()
    assert env.client.get('/api/knowledge-watches').json()[0]['review']['status']=='approved'


@pytest.mark.parametrize('change',['dataset','case','role'])
def test_context_changes_pause_without_new_job(ready,change):
    env=ready;watch=create(env);watches.tick()
    with env.sessions.begin() as db:
        if change=='dataset':
            old=db.get(DatasetVersion,('a-data',1));db.add(DatasetVersion(id=old.id,version=2,name=old.name,spec=old.spec,columns=old.columns))
        elif change=='case':db.get(KnowledgeItem,env.reference['id']).published_version=None
        else:db.get(User,'admin').active=False
    due(env,watch['id']);watches.tick()
    with env.sessions() as db:
        assert not db.get(KnowledgeWatch,watch['id']).enabled
        assert len(list(db.scalars(select(AnalysisRun))))==1


def test_pause_resume_revision_and_owner_checks(ready,monkeypatch):
    env=ready;watch=create(env)
    body={'expected_revision':watch['revision'],'workspace_token':env.run['workspace_token'],'action':'pause'}
    response=env.client.post('/api/knowledge-watches/'+watch['id']+'/actions',json=body)
    assert response.status_code==200 and not response.json()['enabled']
    assert env.client.post('/api/knowledge-watches/'+watch['id']+'/actions',json=body).status_code==409
    due(env,watch['id']);monkeypatch.setattr(watches,'data_fingerprint',lambda *a:pytest.fail('paused watch queried data'))
    watches.tick()
    env.actor.id,env.actor.role='editor','editor'
    assert env.client.get('/api/knowledge-watches').json()==[]
    assert env.client.post('/api/knowledge-watches/'+watch['id']+'/actions',json=body).status_code==403


def test_worker_failure_pauses_and_retry_retains_watch_context(ready,monkeypatch):
    from app.activity_contract import ActivityActionRequest
    from app.job_actions import analysis_action
    env=ready;watch=create(env);watches.tick()
    monkeypatch.setattr(watches,'data_fingerprint',lambda *a:{'fingerprint':'new','row_count':8})
    due(env,watch['id']);watches.tick()
    def fail(*args): raise RuntimeError('private provider detail')
    monkeypatch.setattr('app.evaluation_compute.compute',fail)
    assert worker.tick();watches.tick()
    state=env.client.get('/api/knowledge-watches').json()[0]
    assert not state['enabled'] and 'private' not in state['message']
    with env.sessions() as db:
        failed=db.get(AnalysisRun,state['last_run_id'])
        action={'target':{'kind':'analysis_run','id':failed.id,'project_id':None},'action':'retry',
                'expected_revision':failed.revision,'workspace_token':env.run['workspace_token']}
    retry=analysis_action(ActivityActionRequest.model_validate(action),env.actor)
    monkeypatch.setattr('app.evaluation_compute.compute',lambda c,p,s,cols,stored,check:fake_result(stored)|{'data_fingerprint':'new'})
    assert worker.tick();watches.tick()
    state=env.client.get('/api/knowledge-watches').json()[0]
    assert state['last_run_id']==retry.target.id and state['review'] and not state['enabled']


def test_queue_limit_does_not_advance_fingerprint_or_lose_change(ready,monkeypatch):
    from fastapi import HTTPException
    env=ready;watch=create(env);watches.tick();due(env,watch['id'])
    monkeypatch.setattr(watches,'data_fingerprint',lambda *a:{'fingerprint':'new','row_count':8})
    def full(*a,**k): raise HTTPException(429,'queue full')
    monkeypatch.setattr(watches,'_quota',full)
    watches.tick()
    with env.sessions() as db:
        row=db.get(KnowledgeWatch,watch['id'])
        assert row.enabled and row.fingerprint=='initial' and watches.timestamp(row.next_check_at)<utcnow()


def test_auto_result_draft_cannot_change_numbers(ready):
    env=ready;create(env);watches.tick()
    state=env.client.get('/api/knowledge-watches').json()[0]
    with env.sessions.begin() as db:
        draft=db.scalar(select(CollectedDraft).where(CollectedDraft.question_id==state['review']['question_id']))
        proposal=dict(draft.proposal);proposal['content']={**proposal['content'],'details':{**proposal['content']['details'],'analysis_result':'invented result'}}
        draft.proposal=proposal
        identity,revision=draft.id,draft.revision
    response=approve(env,{'id':identity,'revision':revision})
    assert response.status_code==422


def test_explicit_case_selection_uses_approved_reference_without_search_guessing(ready,monkeypatch):
    from test_evaluation_analysis import body
    monkeypatch.setattr('app.evaluation_analysis.retrieve_evaluation_cases',lambda *a,**k:{'cases':[]})
    request=body(ready,explicit_case=True)
    assert ready.client.post('/api/evaluation-analysis/prepare',json=request).status_code==201
    with ready.sessions.begin() as db:db.get(KnowledgeItem,ready.reference['id']).published_version=None
    assert ready.client.post('/api/evaluation-analysis/prepare',json=request).status_code==409


def test_changed_analysis_method_requires_reconfirmation(ready):
    watch=create(ready);watches.tick()
    with ready.sessions.begin() as db:
        run=db.get(AnalysisRun,ready.run['id'])
        run.result={**run.result,'method_version':'obsolete-method'}
    due(ready,watch['id']);watches.tick()
    with ready.sessions() as db:
        current=db.get(KnowledgeWatch,watch['id'])
        assert not current.enabled and '분석 방법' in current.message
