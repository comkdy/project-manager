"""Initial AI settings share existing execution, natural answers and approval."""
from datetime import timedelta
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app import db as database
from app import evaluation_recommendation as recommendations
from app.analytics import worker
from app.analytics.models import AnalysisRun, KnowledgeWatch
from app.models import DatasetVersion, User, utcnow
from app.semantic import collector_config as llm
from app.semantic.collector_models import CollectedDraft, KnowledgeQuestion
from app.semantic.models import KnowledgeItem
from test_evaluation_analysis import setup, prepare, fake_result
from test_semantic_collection import env, answer, approve, draft, extract

BASE = '/api/evaluation-analysis'


def ready_plan(payload):
    clarification = next((f for f in payload['facts'] if f['kind'] == 'clarification'), None)
    evidence = {'reference': clarification['reference'], 'quote': 'alpha'} if clarification else {'reference':'case','quote':'alpha'}
    return recommendations.Plan.model_validate({'summary':'Recipe별 alpha를 비교합니다.',
        'settings':{'method':'group_compare','metrics':['alpha'],'group_column':'recipe'},
        'columns':[{'column':'alpha','role':'target','reason':'승인 사례의 Inline 관측','evidence':evidence},
                   {'column':'recipe','role':'group','reason':'평가의 Recipe 구분','evidence':{'reference':'case','quote':'Recipe1'}}],
        'row_basis':{'reference':'dataset','quote':'a-src'}, 'limitations':['관측 비교이며 인과 효과가 아닙니다.']})


def request(env, run):
    return env.client.post(BASE+f"/{run['id']}/recommend",json={'expected_revision':run['revision'],'workspace_token':run['workspace_token']})


def current(env, run):
    response=env.client.get(BASE+f"/{run['id']}")
    assert response.status_code==200,response.text
    return response.json()


def confirm(env, run, **changes):
    return env.client.post(BASE+f"/{run['id']}/execute",json={'expected_revision':run['revision'],
        'workspace_token':run['workspace_token'],'settings':run['recommendation']['settings'], 'continuous':True, **changes})


def test_grounded_recommendation_confirms_once_and_connects_to_worker_and_review(setup,monkeypatch):
    seen=[]
    def model(prompt,payload,schema):
        assert schema is recommendations.Plan
        assert 'rows' not in payload and 'facts' in payload
        seen.append(payload)
        return ready_plan(payload)
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup)
    assert request(setup,run).status_code==202
    run=current(setup,run)
    assert run['recommendation']['status']=='ready' and run['settings'] is None
    recovered=setup.client.get(BASE+'?setup_only=true').json()
    assert [entry['id'] for entry in recovered]==[run['id']]
    with setup.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeWatch))==0
    assert confirm(setup,run).status_code==202
    assert confirm(setup,run).status_code==202
    assert setup.client.get(BASE+'?setup_only=true').json()==[]
    with setup.sessions() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeWatch))==1
    monkeypatch.setattr('app.evaluation_compute.compute',lambda *args:fake_result(args[4])|{'data_fingerprint':'result-data'})
    assert worker.tick()
    from app.knowledge_watch import tick
    tick()
    result=current(setup,run)
    assert result['status']=='completed' and result['review']['status']=='pending'
    assert len(seen)==1


def test_ambiguous_mapping_natural_answer_approval_then_recommend_and_revoke(setup,monkeypatch):
    def model(prompt,payload,schema):
        if schema is not recommendations.Plan:
            return extract(prompt,payload,schema)
        if any(f['kind']=='clarification' for f in payload['facts']):
            return ready_plan(payload)
        return recommendations.Plan(summary='alpha의 데이터 연결 확인이 필요합니다.',questions=[
            recommendations.Clarification(question='alpha는 어떤 측정 항목인가요?',reason='Inline 측정 대응 확인',columns=['alpha'])])
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup)
    assert request(setup,run).status_code==202
    run=current(setup,run)
    assert run['recommendation']['status']=='waiting'
    qid=run['recommendation']['questions'][0]['id']
    assert request(setup,run).status_code==202
    assert len(current(setup,run)['recommendation']['questions'])==1
    unsafe=setup.client.post(BASE+f"/{run['id']}/execute",json={'expected_revision':run['revision'],
        'workspace_token':run['workspace_token'],'settings':{'method':'correlation','metrics':['alpha','beta']}})
    assert unsafe.status_code==409
    q=setup.client.get('/api/semantic/collection/questions/'+qid).json()
    assert q['analysis_setup']['run_id']==run['id']
    answer(setup,q,'alpha는 같은 wafer의 Inline CD 값이며 recipe는 그 전 공정의 Recipe입니다.')
    # A structured but unapproved answer must not reach the recommender.
    assert request(setup,run).status_code==202
    assert current(setup,run)['recommendation']['status']=='waiting'
    d=draft(setup)
    approved=approve(setup,d)
    assert approved.status_code==200,approved.text
    assert request(setup,run).status_code==202
    run=current(setup,run)
    assert run['recommendation']['status']=='ready'
    assert 'clarification:' in run['recommendation']['columns'][0]['evidence']['reference']
    from app.semantic.evaluation_terms import approved_definitions
    from app.semantic.service import get_semantic_context, catalog_snapshot
    from app.semantic.schemas import ContextRequest
    with setup.sessions() as db:
        assert approved.json()['item_id'] not in approved_definitions(db)
        context=get_semantic_context(db,ContextRequest(query='검사 alpha',connection_key='a',workspace_token=database.workspace_token()),setup.actor,catalog=catalog_snapshot(db))
        assert approved.json()['item_id'] not in [i['id'] for i in context['items']]
    # Same case/dataset can reuse the scoped explanation in a subsequent setup.
    fresh=prepare(setup)
    with setup.sessions() as db:
        facts,_=recommendations.facts_for(db,db.get(AnalysisRun,fresh['id']))
        assert any(f['reference']=='knowledge:'+approved.json()['item_id'] for f in facts)
    # Retry and scheduled children keep the approved clarification even though
    # their question_ids are intentionally empty (their own new questions only).
    with setup.sessions() as db:
        original=db.get(AnalysisRun,run['id'])
        child=AnalysisRun(connection_key=original.connection_key,kind='evaluation',request=deepcopy(original.request),
            source_snapshot=deepcopy(original.source_snapshot),created_by=original.created_by,question_ids=[])
        recommendations.check_basis(db,child)
    with setup.sessions.begin() as db:
        db.get(KnowledgeItem,approved.json()['item_id']).published_version=None
    assert confirm(setup,run).status_code==409


@pytest.mark.parametrize('bad',['column','quote','reference','group_value','coverage','semantic'])
def test_model_guessing_cannot_queue_or_publish(setup,monkeypatch,bad):
    def model(prompt,payload,schema):
        value=ready_plan(payload).model_dump()
        if bad=='column':value['settings']['metrics']=['secret_column']
        elif bad=='quote':value['columns'][0]['evidence']['quote']='a made up fact'
        elif bad=='reference':value['columns'][0]['evidence']['reference']='missing'
        elif bad=='group_value':value['settings'].update(baseline='madeup1',comparison='madeup2')
        elif bad=='coverage':value['columns']=value['columns'][:1]
        else:value['columns'][0]['evidence']={'reference':'dataset','quote':'alpha'}
        return recommendations.Plan.model_validate(value)
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup);assert request(setup,run).status_code==202
    value=current(setup,run)
    assert value['recommendation']['status']=='error' and value['status']=='needs_input'
    with setup.sessions() as db:assert db.scalar(select(func.count()).select_from(KnowledgeWatch))==0


def test_invalid_column_roles_are_repaired_by_model_before_ready(setup,monkeypatch):
    prompts=[]
    def model(prompt,payload,schema):
        prompts.append(prompt)
        value=ready_plan(payload).model_dump()
        if len(prompts)==1:
            value['columns'][0]['role']='feature'
        return recommendations.Plan.model_validate(value)
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup)
    assert request(setup,run).status_code==202
    result=current(setup,run)
    assert len(prompts)==2 and '이전 응답' in prompts[1]
    assert result['recommendation']['status']=='ready'
    assert result['recommendation']['columns'][0]['role']=='target'
    assert result['status']=='needs_input'


def test_invalid_repair_remains_blocked_after_two_attempts(setup,monkeypatch):
    attempts=[]
    def model(prompt,payload,schema):
        attempts.append(prompt)
        value=ready_plan(payload).model_dump()
        value['row_basis']['quote']='Invented alignment'
        return recommendations.Plan.model_validate(value)
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup)
    assert request(setup,run).status_code==202
    result=current(setup,run)
    assert len(attempts)==2 and result['recommendation']['status']=='error'
    assert result['status']=='needs_input'


def test_provider_failure_is_not_retried(setup,monkeypatch):
    attempts=[]
    def model(*args):
        attempts.append(True)
        raise llm.CollectionError('Provider unavailable')
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup)
    assert request(setup,run).status_code==202
    assert current(setup,run)['recommendation']['status']=='error'
    assert len(attempts)==1


def test_recommendation_expiry_stale_response_and_authorization(setup,monkeypatch):
    run=prepare(setup)
    body=recommendations.Recommend(expected_revision=run['revision'],workspace_token=run['workspace_token'])
    first,token=recommendations.claim(run['id'],body,setup.actor)
    again,no_token=recommendations.claim(run['id'],body.model_copy(update={'expected_revision':first['revision']}),setup.actor)
    assert no_token is None and again['revision']==first['revision']
    with setup.sessions.begin() as db:
        row=db.get(AnalysisRun,run['id'])
        row.request={**row.request,'_evaluation_recommendation':{**row.request['_evaluation_recommendation'],'expires_at':(utcnow()-timedelta(minutes=1)).isoformat()}}
    assert current(setup,run)['recommendation']['status']=='error'
    value,next_token=recommendations.claim(run['id'],body.model_copy(update={'expected_revision':first['revision']}),setup.actor)
    monkeypatch.setattr(llm,'complete',lambda *args:pytest.fail('old response must not call model'))
    recommendations.process(run['id'],token,database.current_source())
    assert current(setup,run)['recommendation']['status']=='running'
    setup.actor.id,setup.actor.role='editor','editor'
    assert request(setup,value).status_code==403
    setup.actor.id,setup.actor.role='reader','viewer'
    assert request(setup,value).status_code==403


def test_dataset_or_knowledge_change_during_model_call_rejects_result(setup,monkeypatch):
    def model(prompt,payload,schema):
        with setup.sessions.begin() as db:
            old=db.get(DatasetVersion,('a-data',1))
            db.add(DatasetVersion(id=old.id,version=2,name=old.name,spec=old.spec,columns=old.columns))
        return ready_plan(payload)
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup);assert request(setup,run).status_code==202
    with setup.sessions() as db:
        row=db.get(AnalysisRun,run['id'])
        assert row.request['_evaluation_recommendation']['status']=='error'
        assert row.status=='needs_input'


def test_ml_settings_require_grounded_time_unit_and_prior_features(setup,monkeypatch):
    def model(prompt,payload,schema):
        value=ready_plan(payload).model_dump()
        value['settings']={'method':'ml_regression','metrics':['alpha'],'features':['recipe'],'unit_columns':['id'],'time_column':'end_dt_tm'}
        value['columns'][1]['role']='feature'
        for col,role in [('id','unit'),('end_dt_tm','time')]:
            value['columns'].append({'column':col,'role':role,'reason':'저장된 데이터 정의','evidence':{'reference':'dataset','quote':col}})
        value['features_precede_target']=True
        return recommendations.Plan.model_validate(value)
    monkeypatch.setattr(llm,'complete',model)
    run=prepare(setup);assert request(setup,run).status_code==202
    run=current(setup,run)
    assert run['recommendation']['status']=='ready'
    assert confirm(setup,run,continuous=False).status_code==202
    with setup.sessions() as db:assert db.scalar(select(func.count()).select_from(KnowledgeWatch))==0


def test_first_recommended_run_retry_can_resume_continuous_checks(setup,monkeypatch):
    from app import knowledge_watch as watches
    from app.activity_contract import ActivityActionRequest
    from app.job_actions import analysis_action
    setup.client.app.include_router(watches.router)
    monkeypatch.setattr(llm,'complete',lambda prompt,payload,schema:ready_plan(payload))
    run=prepare(setup);assert request(setup,run).status_code==202
    run=current(setup,run);assert confirm(setup,run).status_code==202
    def fail(*args):raise RuntimeError('synthetic failure')
    monkeypatch.setattr('app.evaluation_compute.compute',fail)
    assert worker.tick();watches.tick()
    failed=current(setup,run)
    retried=analysis_action(ActivityActionRequest.model_validate({'target':{'kind':'analysis_run','id':run['id'],'project_id':None},
        'action':'retry','expected_revision':failed['revision'],'workspace_token':run['workspace_token']}),setup.actor)
    monkeypatch.setattr('app.evaluation_compute.compute',lambda *args:fake_result(args[4])|{'data_fingerprint':'recovered'})
    assert worker.tick();watches.tick()
    watch=setup.client.get('/api/knowledge-watches').json()[0]
    assert not watch['enabled'] and watch['last_run_id']==retried.target.id and watch['review']
    monkeypatch.setattr(watches,'_compile',lambda *a,**k:(None,'fixture',{},None))
    monkeypatch.setattr(watches,'data_fingerprint',lambda *a:{'fingerprint':'recovered','row_count':4})
    resumed=setup.client.post('/api/knowledge-watches/'+watch['id']+'/actions',json={
        'action':'resume','expected_revision':watch['revision'],'workspace_token':run['workspace_token']})
    assert resumed.status_code==200,resumed.text
    watches.tick()
    final=setup.client.get('/api/knowledge-watches').json()[0]
    assert final['enabled'] and '변경 없음' in final['message']
