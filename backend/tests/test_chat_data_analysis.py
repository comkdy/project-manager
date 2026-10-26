"""Chat analysis executes existing worker jobs; publication still needs review."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import chat_data_analysis as chat, query
from app.analytics import worker
from app.analytics.models import AnalysisRun
from app.assistant import ChatRequest
from app.chat_data_contracts import DataAnalysisPlan
from app.models import DatasetVersion, Source, User
from app.semantic.collector_models import KnowledgeQuestion
from app.semantic.models import KnowledgeItem
from app.semantic.service import digest
from test_evaluation_analysis import setup, fake_result
from test_semantic_collection import env, approve


def inputs(env, method='correlation', *, question=False):
    with env.sessions() as db:
        dataset = chat.inventory(db)['a-data']
    settings = ({'method':'correlation','metrics':['alpha','beta']} if method=='correlation' else
                {'method':'group_compare','metrics':['alpha'],'group_column':'recipe'} if method=='group_compare' else
                {'method':'ml_regression','metrics':['beta'],'features':['alpha'],'unit_columns':['id'],'time_column':'end_dt_tm'})
    request = ChatRequest(message='alpha는 beta 이전 측정이며 같은 id의 행에 대응됩니다. recipe별 또는 alpha와 beta 관계를 분석해줘.',dataset_id='a-data')
    evidence={'reference':'user','quote':request.message}
    roles=[(c,'target') for c in settings['metrics']]+[(c,'feature') for c in settings.get('features',[])]+[(c,'unit') for c in settings.get('unit_columns',[])]
    if settings.get('group_column'):roles.append((settings['group_column'],'group'))
    if settings.get('time_column'):roles.append((settings['time_column'],'time'))
    plan={'summary':'현재 데이터에서 두 특성의 관계를 확인합니다.','settings':None if question else settings,
          'columns':[] if question else [{'column':c,'role':r,'reason':'사용자가 명시한 의미','evidence':evidence} for c,r in roles],
          'row_basis':None if question else evidence,'features_precede_target':method=='ml_regression' and not question,
          'questions':[{'question':'두 측정값은 같은 wafer의 값인가요?','reason':'대응 기준 확인','columns':['alpha','beta']}] if question else []}
    value=DataAnalysisPlan(kind='data_analysis_plan',dataset_id='a-data',dataset_version=1,plan=plan)
    payload={'datasets':[dataset],'facts':[{'reference':'user','kind':'user','text':request.message},
              {'reference':'dataset:a-data','kind':'dataset','text':chat.text(dataset)}]}
    return value,request,payload


def create(env, method='correlation', **kwargs):
    value,request,payload=inputs(env,method,**kwargs)
    identity=chat.create(value,request,'admin',payload,{},[],origin_project_id=None)
    return identity,value,request,payload


@pytest.mark.parametrize('method',['group_compare','correlation','ml_regression'])
def test_chat_queues_same_worker_without_case_or_dataset_creation(setup, monkeypatch, method):
    env=setup
    with env.sessions() as db:
        before_datasets=db.scalar(select(func.count()).select_from(DatasetVersion))
        before_knowledge=db.scalar(select(func.count()).select_from(KnowledgeItem))
    seen=[]
    def compute(cte,params,settings,columns,stored,checkpoint):
        seen.append(settings.method);checkpoint()
        assert stored['_evaluation_case'] is None and stored['_chat_analysis']['question']
        return fake_result(stored)
    monkeypatch.setattr('app.evaluation_compute.compute',compute)
    identity,*_=create(env,method)
    assert chat.read(identity,'admin',None)['status']=='queued' and not seen
    assert worker.tick()
    answer=chat.read(identity,'admin',None)
    assert answer['status']=='completed' and answer['result']['row_count']==4 and seen==[method]
    assert answer['review'] is None and answer['dataset']['id']=='a-data'
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DatasetVersion))==before_datasets
        assert db.scalar(select(func.count()).select_from(KnowledgeItem))==before_knowledge


def test_result_only_becomes_knowledge_after_existing_review(setup, monkeypatch):
    env=setup
    monkeypatch.setattr('app.evaluation_compute.compute',lambda c,p,s,cols,stored,checkpoint:fake_result(stored))
    identity,*_=create(env)
    assert worker.tick()
    answer=chat.read(identity,'admin',None)
    review=env.client.post(f'/api/evaluation-analysis/{identity}/submit-review',json={
        'expected_revision':answer['revision'],'workspace_token':answer['workspace_token']})
    assert review.status_code==201,review.text
    pending=env.client.get('/api/semantic/collection/drafts/'+review.json()['draft_id']).json()
    assert pending['status']=='pending'
    assert pending['proposal']['content']['references']==[]
    assert identity in pending['proposal']['content']['details']['evidence']
    result=approve(env,pending)
    assert result.status_code==200,result.text
    assert chat.read(identity,'admin',None)['review']['status']=='approved'


def test_ambiguous_connection_asks_in_existing_inbox_without_running(setup):
    env=setup
    identity,*_=create(env,question=True)
    answer=chat.read(identity,'admin',None)
    assert answer['status']=='needs_input' and len(answer['questions'])==1
    assert not worker.tick()
    question=env.client.get('/api/semantic/collection/questions/'+answer['questions'][0]['id']).json()
    assert question['status']=='open' and question['analysis_setup']['run_id']==identity


def test_unresolved_question_cannot_attach_speculative_column_plan(setup):
    from pydantic import ValidationError
    waiting,_,_=inputs(setup,question=True)
    ready,_,_=inputs(setup)
    for field in ('settings','columns','row_basis'):
        data=waiting.model_dump()
        data['plan'][field]=ready.model_dump()['plan'][field]
        with pytest.raises(ValidationError,match='speculative column mappings'):
            DataAnalysisPlan.model_validate(data)


@pytest.mark.parametrize('change',['dataset','source','owner'])
def test_history_and_result_fail_closed_after_scope_changes(setup,change):
    env=setup
    identity,*_=create(env)
    with env.sessions.begin() as db:
        if change=='dataset':
            old=db.get(DatasetVersion,('a-data',1));db.add(DatasetVersion(id=old.id,version=2,name=old.name,spec=old.spec,columns=old.columns))
        elif change=='source':db.get(Source,'a-src').available=False
        else:db.get(User,'admin').active=False
    restored=chat.restore({'run_id':identity},'admin',None)
    assert restored['status']=='unavailable' and 'result' not in restored


def test_unknown_columns_dataset_scope_and_unquoted_filter_are_rejected(setup):
    value,request,payload=inputs(setup)
    bad=value.model_copy(update={'dataset_id':'b-data'})
    with pytest.raises(ValueError):chat.validate(bad,payload)
    data=value.model_dump();data['plan']['settings']['metrics']=['alpha','missing']
    with pytest.raises(HTTPException):chat.validate(DataAnalysisPlan.model_validate(data),payload)
    data=value.model_dump();data['filters']=[{'filter':{'column':'recipe','op':'eq','value':'invented'},'evidence':{'reference':'user','quote':request.message}}]
    with pytest.raises(ValueError):chat.validate(DataAnalysisPlan.model_validate(data),payload)


def test_chat_message_preserves_current_card_filters_and_scope(setup,monkeypatch):
    from app.schemas import CardSpec
    value,request,payload=inputs(setup)
    request.card=CardSpec(id='selected',title='선택 카드',kind='scatter',dataset_id='a-data',dataset_version=1,x='alpha',y='beta',
        filters=[{'column':'parameter','op':'eq','value':'P1'}],period={'use_dashboard':False})
    identity=chat.create(value,request,'admin',payload,{},[],origin_project_id=None)
    answer=chat.read(identity,'admin',None)
    assert answer['scope']['filters']==[{'column':'parameter','op':'eq','value':'P1'}]
    with pytest.raises(HTTPException):chat.read(identity,'editor',None)
    with pytest.raises(HTTPException):chat.read(identity,'admin',1)


def test_approved_answer_resumes_worker_and_retry_keeps_approved_basis(setup, monkeypatch):
    from app import evaluation_recommendation as recommendation, evaluation_analysis
    from app.semantic import collector_config as llm
    from test_semantic_collection import answer, draft, extract
    from test_evaluation_analysis import prepare
    env=setup
    identity,*_=create(env,question=True)
    current=chat.read(identity,'admin',None)
    q=env.client.get('/api/semantic/collection/questions/'+current['questions'][0]['id']).json()
    def model(prompt,payload,schema):
        if schema is not recommendation.Plan:return extract(prompt,payload,schema)
        fact=next(f for f in payload['facts'] if f['kind']=='clarification')
        plan=inputs(env)[0].plan.model_dump()
        for col in plan['columns']:col['evidence']={'reference':fact['reference'],'quote':'같은 wafer'}
        plan['row_basis']={'reference':fact['reference'],'quote':'같은 wafer'}
        return recommendation.Plan.model_validate(plan)
    monkeypatch.setattr(llm,'complete',model)
    answer(env,q,'alpha와 beta는 같은 wafer의 측정값입니다.')
    assert not worker.tick()
    assert approve(env,draft(env)).status_code==200
    body={'expected_revision':current['revision'],'workspace_token':current['workspace_token']}
    assert env.client.post(f'/api/evaluation-analysis/{identity}/recommend',json=body).status_code==202
    assert chat.read(identity,'admin',None)['status']=='queued'
    monkeypatch.setattr('app.evaluation_compute.compute',lambda c,p,s,cols,stored,checkpoint:fake_result(stored))
    assert worker.tick()
    assert chat.read(identity,'admin',None)['status']=='completed'
    # A different case must ignore these chat-scoped answers safely.
    other=prepare(env)
    with env.sessions.begin() as db:
        recommendation.facts_for(db,db.get(AnalysisRun,other['id']))
        original=db.get(AnalysisRun,identity)
        child=evaluation_analysis.retry_run(db,original,db.get(User,'admin'))
        recommendation.check_basis(db,child)
        assert child.question_ids==[] and child.request['_evaluation_plan_questions']


def test_model_route_executes_chat_worker_without_dataset_or_card_proposal(setup,monkeypatch):
    import asyncio
    from app import chat_workflow
    from app.chat_data_contracts import DataAnalysisCall
    value,request,payload=inputs(setup)
    monkeypatch.setattr(chat,'context_for',lambda *a:(payload,{},[]))
    monkeypatch.setattr('app.catalog.projects_list',lambda:[])
    monkeypatch.setattr(chat,'resolve_project',lambda *a:None)
    stages=[]
    class Adapter:
        actor_id='admin'
        async def run(self,stage,extra=None,validate=None):
            stages.append(stage)
            if stage=='route':return DataAnalysisCall(kind='data_analysis'),None
            assert 'rows' not in extra['data_analysis_context']
            return value,validate(value)
    async def collect():return [v async for v in chat_workflow.run_workflow(request,Adapter())]
    events=asyncio.run(collect())
    assert stages==['route','data_analysis_plan']
    assert events[-1][0]=='data_analysis' and events[-1][1]['status']=='queued'
    assert not any(event=='proposal' for event,_ in events)


def test_real_json_adapter_repairs_unsupported_plan_without_executing_it(setup,monkeypatch):
    import asyncio
    import json
    import httpx
    from app import chat_workflow
    value,request,payload=inputs(setup)
    monkeypatch.setattr(chat,'context_for',lambda *a:(payload,{},[]))
    monkeypatch.setattr('app.catalog.projects_list',lambda:[])
    monkeypatch.setattr(chat,'resolve_project',lambda *a:None)
    invalid=value.model_dump();invalid['plan']['columns'][0]['evidence']['quote']='invented evidence'
    replies=[{'kind':'data_analysis'},invalid,value.model_dump()]
    seen=[]
    def transport(req):
        seen.append(json.loads(req.content))
        assert len(seen)<=3
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(replies[len(seen)-1])}}]})
    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter=chat_workflow.ModelAdapter(client,'http://model.test/v1/chat/completions','test',{},request,
                {'query_result':{'rows':[{'raw_measurement':'must-not-reach-planner'}]}})
            adapter.actor_id='admin'
            return [v async for v in chat_workflow.run_workflow(request,adapter)]
    events=asyncio.run(collect())
    assert events[-1][0]=='data_analysis' and events[-1][1]['status']=='queued'
    assert len(seen)==3 and 'data_analysis_plan' in seen[1]['messages'][0]['content']
    assert 'must-not-reach-planner' not in json.dumps(seen)
    assert 'Ungrounded citation' in seen[2]['messages'][-1]['content']
    with setup.sessions() as db:assert db.scalar(select(func.count()).select_from(AnalysisRun))==1


def test_live_model_correlation_ml_mix_and_spliced_citations_are_rejected(setup):
    from pydantic import ValidationError
    value,request,payload=inputs(setup)
    data=value.model_dump()
    data['plan']['settings'].update(metrics=['beta'],features=['alpha'],unit_columns=['id'],time_column='end_dt_tm')
    with pytest.raises(ValidationError):DataAnalysisPlan.model_validate(data)
    schema=DataAnalysisPlan.model_json_schema()['$defs']['CorrelationSettings']['properties']
    assert schema['metrics']['minItems']==schema['metrics']['maxItems']==2
    assert schema['features']['maxItems']==schema['unit_columns']['maxItems']==0
    data=value.model_dump()
    data['plan']['row_basis']['quote']='alpha ... 같은 id'
    with pytest.raises(ValueError,match='Ungrounded citation'):
        chat.validate(DataAnalysisPlan.model_validate(data),payload)
