"""Case -> dataset recommendation/draft -> natural clarification -> existing analysis."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import db as database, chat_tools, evaluation_datasets as discovery
from app.analytics.models import AnalysisRun, EvaluationDatasetRecommendation
from app.chat_contracts import ProposalApply, ProposalDefinition
from app.evaluation_recommendation import facts_for as analysis_facts
from app.models import DatabaseMount, DatasetVersion, Source, User
from app.semantic import collector_config as llm
from app.semantic.collector_models import KnowledgeQuestion
from app.semantic.models import KnowledgeItem
from test_evaluation_analysis import setup, COLS
from test_semantic_collection import env, answer, approve, draft, extract

BASE = '/api/evaluation-datasets'


@pytest.fixture
def prepared(setup, monkeypatch):
    setup.client.app.include_router(discovery.router)
    # Current registrations have case scope and natural language, not a pinned dataset.
    from app.semantic.evaluations import evaluation_origin
    with setup.sessions.begin() as db:
        origin = evaluation_origin(db, setup.reference['id'], setup.reference['version'])
        origin.dataset = {'context_kind': 'evaluation_case', 'project_id': None}
    monkeypatch.setattr(llm, 'complete', lambda prompt, payload, schema: existing_plan(payload))
    return setup


def existing_plan(payload):
    clarification = next((f for f in payload['facts'] if f['kind'] == 'clarification'), None)
    return discovery.DatasetPlan(summary='같은 표본의 Recipe와 alpha가 있는 데이터셋입니다.', dataset_id='a-data',
        evidence=[{'reference':'dataset:a-data','quote':'a-data'}, {'reference':'case','quote':'alpha'}],
        alignment_basis={'reference':clarification['reference'],'quote':'같은 wafer'} if clarification else {'reference':'case','quote':'alpha'})


def start(e):
    response = e.client.post(BASE, json={'case_ref': e.reference, 'workspace_token':database.workspace_token()})
    assert response.status_code == 202, response.text
    return read(e, response.json()['id'])


def read(e, identity):
    response = e.client.get(BASE+'/'+identity)
    assert response.status_code == 200, response.text
    return response.json()


def again(e, row):
    return e.client.post(BASE+'/'+row['id']+'/recommend', json={'expected_revision':row['revision'], 'workspace_token':row['workspace_token']})


def prepare(e, row, **changes):
    return e.client.post('/api/evaluation-analysis/prepare', json={'explicit_case':True, 'dataset_recommendation_id':row['id'],
        'case_ref':e.reference, 'suggestion_id':e.reference['id']+':recheck',
        'search':{'query':'사례 분석', 'dataset_id':row['dataset_id']}, **changes})


def test_existing_dataset_recommended_without_creating_data_and_preparation_idempotent(prepared):
    e=prepared
    row=start(e)
    assert row['status']=='ready' and row['dataset']['id']=='a-data' and row['proposal'] is None
    assert start(e)['id']==row['id']
    assert [r['id'] for r in e.client.get(BASE).json()]==[row['id']]
    with e.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DatasetVersion))==2
        assert db.scalar(select(func.count()).select_from(AnalysisRun))==0
    result=prepare(e,row)
    assert result.status_code==201,result.text
    assert prepare(e,row).json()['id']==result.json()['id']
    assert e.client.get(BASE).json()==[]


def test_ambiguous_connection_uses_natural_answer_approval_and_inherits_analysis_basis(prepared,monkeypatch):
    e=prepared
    def model(prompt,payload,schema):
        if schema is not discovery.DatasetPlan:
            return extract(prompt,payload,schema)
        if any(f['kind']=='clarification' for f in payload['facts']):
            return existing_plan(payload)
        return discovery.DatasetPlan(summary='표본 대응 설명이 필요합니다.',questions=[
            {'question':'Recipe와 alpha는 같은 wafer의 측정인가요?', 'reason':'서로 다른 표본을 섞지 않기 위해 확인합니다.'}])
    monkeypatch.setattr(llm,'complete',model)
    row=start(e)
    assert row['status']=='waiting' and row['dataset_id'] is None
    q=e.client.get('/api/semantic/collection/questions/'+row['questions'][0]['id']).json()
    assert q['dataset'] is None and q['dataset_setup']['recommendation_id']==row['id']
    assert again(e,row).status_code==202
    answer(e,q,'recipe와 alpha는 같은 wafer의 공정과 Inline 값입니다.')
    assert again(e,row).status_code==202
    assert read(e,row['id'])['status']=='waiting'
    approval=approve(e,draft(e))
    assert approval.status_code==200,approval.text
    assert again(e,row).status_code==202
    row=read(e,row['id'])
    assert row['status']=='ready'
    response=prepare(e,row)
    assert response.status_code==201,response.text
    with e.sessions() as db:
        facts,_=analysis_facts(db,db.get(AnalysisRun,response.json()['id']))
        assert any(f['reference'].startswith('discovery:clarification:') and '같은 wafer' in f['text'] for f in facts)
        from app.semantic.evaluation_terms import approved_definitions
        assert approval.json()['item_id'] not in approved_definitions(db)
    with e.sessions.begin() as db:
        db.get(KnowledgeItem,approval.json()['item_id']).published_version=None
    assert prepare(e,row).status_code==409


@pytest.mark.parametrize('mutation',[{'dataset_id':'b-data'}, {'evidence':[{'reference':'dataset:a-data','quote':'invented'}]},
    {'alignment_basis':{'reference':'not-approved','quote':'alpha'}}])
def test_invented_hidden_or_ungrounded_recommendations_never_become_ready(prepared,monkeypatch,mutation):
    monkeypatch.setattr(llm,'complete',lambda p,v,s:discovery.DatasetPlan.model_validate(existing_plan(v).model_dump()|mutation))
    assert start(prepared)['status']=='error'


def test_stale_case_dataset_source_workspace_and_owner_rejected(prepared):
    e=prepared
    row=start(e)
    e.actor.id,e.actor.role='editor','editor'
    assert e.client.get(BASE+'/'+row['id']).status_code==403
    e.actor.id,e.actor.role='reader','viewer'
    assert e.client.post(BASE,json={'case_ref':e.reference,'workspace_token':database.workspace_token()}).status_code==403
    e.actor.id,e.actor.role='admin','admin'
    with e.sessions.begin() as db:
        dataset=db.get(DatasetVersion,('a-data',1));dataset.name='changed'
    assert prepare(e,row).status_code==409
    refreshed=start(e)
    assert refreshed['id']!=row['id']
    with e.sessions.begin() as db:
        db.get(Source,'a-src').columns=COLS+[{'name':'new','type':'text'}]
    assert e.client.get(BASE+'/'+refreshed['id']).status_code==409


def test_model_race_does_not_commit_stale_recommendation(prepared,monkeypatch):
    def model(prompt,payload,schema):
        with prepared.sessions.begin() as db:
            db.get(DatasetVersion,('a-data',1)).name='changed during model call'
        return existing_plan(payload)
    monkeypatch.setattr(llm,'complete',model)
    assert start(prepared)['status']=='error'


def query_plan(payload):
    return discovery.DatasetPlan(summary='필요한 두 측정값을 함께 볼 생성안입니다.',
        query={'kind':'query','name':'비교 데이터','explanation':'같은 표본 비교','source_id':'a-src','sql':'SELECT id, recipe, alpha FROM public.measurements'},
        evidence=[{'reference':'case','quote':'alpha'}],alignment_basis={'reference':'case','quote':'alpha'})


def test_creation_draft_uses_existing_preview_apply_and_then_analysis(prepared,monkeypatch):
    e=prepared
    monkeypatch.setattr(llm,'complete',lambda p,v,s:query_plan(v))
    def validate(source,sql,name,project,pivot=None):
        assert source=='a-src' and sql.startswith('SELECT')
        spec={'source_id':source,'raw_sql':sql,'columns':[],'filters':[],'joins':[]}
        return {'definition':{'name':name,'spec':spec},'columns':COLS,
                'preview':{'spec':spec,'columns':COLS,'rows':[],'meta':{},'next_cursor':None}}
    monkeypatch.setattr(chat_tools,'validate_query',validate)
    monkeypatch.setattr(chat_tools,'SessionLocal',e.sessions)
    row=start(e)
    assert row['status']=='ready' and row['dataset_id'] is None and row['proposal']['dataset']['name']=='비교 데이터'
    with e.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DatasetVersion))==2
    raw={k:v for k,v in row['proposal'].items() if k in ProposalDefinition.model_fields}
    proposal=ProposalDefinition.model_validate(raw)
    preview=chat_tools.preview_proposal(proposal,None,actor_id=e.actor.id)
    assert preview['evaluation_dataset_recommendation_id']==row['id']
    # Exercise the production atomic apply against a disposable metadata DB.
    with e.sessions.begin() as db:
        DatabaseMount.__table__.create(db.bind,checkfirst=True)
        connection=db.connection().connection.driver_connection
        connection.create_function('pg_advisory_xact_lock',1,lambda x:0)
        connection.create_function('hashtextextended',2,lambda x,y:1)
    body=ProposalApply(proposal=proposal,request_id=uuid4(),workspace_token=database.workspace_token())
    result=chat_tools.apply_proposal(body,e.actor)
    assert chat_tools.apply_proposal(body,e.actor)==result
    row=read(e,row['id'])
    assert row['status']=='applied' and row['dataset_id']==result['dataset_id'] and row['proposal'] is None
    result=prepare(e,row)
    assert result.status_code==201,result.text
    with e.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DatasetVersion))==3


def test_query_validation_failure_does_not_create_or_publish_data(prepared,monkeypatch):
    monkeypatch.setattr(llm,'complete',lambda p,v,s:query_plan(v))
    def reject(*args):
        raise ValueError('invalid query')
    monkeypatch.setattr(chat_tools,'validate_query',reject)
    assert start(prepared)['status']=='error'
    with prepared.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DatasetVersion))==2
        assert db.scalar(select(func.count()).select_from(KnowledgeQuestion))==1  # original case only


def test_question_overrides_tentative_dataset_and_sql_never_executes(prepared,monkeypatch):
    def model(prompt,payload,schema):
        return discovery.DatasetPlan.model_validate({'summary':'연결 확인 필요','dataset_id':'a-data',
            'query':{'unvalidated_sql':'must not run'},'evidence':[{'unvalidated':'ignored'}],
            'questions':[{'question':'표본은 어떻게 연결하나요?','reason':'모호한 연결','columns':['unknown']}]})
    monkeypatch.setattr(llm,'complete',model)
    monkeypatch.setattr(chat_tools,'validate_query',lambda *args:pytest.fail('Question cannot execute SQL'))
    row=start(prepared)
    assert row['status']=='waiting' and row['dataset_id'] is None and row['proposal'] is None


def test_completed_analysis_remains_readable_after_returning_to_same_database(prepared,monkeypatch):
    row=start(prepared)
    response=prepare(prepared,row)
    assert response.status_code==201,response.text
    identity=response.json()['id']
    monkeypatch.setattr(database,'workspace_token',lambda:'new-epoch-same-source')
    assert prepared.client.get('/api/evaluation-analysis/'+identity).status_code==409
    with prepared.sessions.begin() as db:
        db.get(AnalysisRun,identity).status='completed'
    assert prepared.client.get('/api/evaluation-analysis/'+identity).status_code==200
