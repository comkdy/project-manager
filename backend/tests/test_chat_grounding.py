import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.assistant import ChatRequest, ChatMessage
from app.analytics.chat_contracts import AnalysisCall
from app.chat_grounding import ground_analysis, GroundingQuestion, mentions, resolve_project
from app.chat_workflow import ModelAdapter


CATALOG={'supported':True,
 'projects':[{'id':5,'name':'XXXA','code':'XXXA','lot_cds':['XXXA']},{'id':6,'name':'XXXB','code':'XXXB','lot_cds':['XXXB']}],
 'series':[{'family':'inline','operation':'oper A CD','parameter':'MAIN'}, {'family':'epm','operation':'','parameter':'PARA1'}],
 'recipe_operations':[{'operation':'oper A','recipes':['RCP1','RCP2','RCP3']}]}


def request(text='XXXA에서 operA 에 따른 특성 변화가 관측돼?',**kwargs):
    return ChatRequest(message=text,**kwargs)


def plan(**kwargs):
    return AnalysisCall(kind='analysis',analysis_kind='split',operation='operA',**kwargs)


def test_actual_question_resolves_project_and_unique_operation_not_para():
    result,effective=ground_analysis(plan(),request(),CATALOG)
    assert effective.project_id==5 and request().project_id is None
    assert result.operation=='oper A' and result.parameter is None and result.family is None
    assert result.goal=='describe' and result.recipe_a is result.recipe_b is None


def test_actual_wrong_model_interpretation_is_rejected_before_proposal():
    with pytest.raises(ValueError,match='프로젝트 범위'):
        ground_analysis(AnalysisCall(kind='analysis',analysis_kind='trend',family='inline',parameter='XXXA',operation='operA'),request(),CATALOG)


@pytest.mark.parametrize('text',['xxxa에서 OPERA에 따른 특성 비교','ＸＸＸＡ에서 oper A 특성 비교'])
def test_case_and_whitespace_are_only_resolved_to_real_names(text):
    value,effective=ground_analysis(plan(),request(text),CATALOG)
    assert effective.project_id==5 and value.operation=='oper A'


def test_unknown_and_hidden_measurement_names_do_not_become_targets():
    with pytest.raises(ValueError,match='등록된 목록'):
        ground_analysis(plan(parameter='SECRET'),request(),CATALOG)
    with pytest.raises(ValueError,match='등록된 목록'):
        ground_analysis(AnalysisCall(kind='analysis',analysis_kind='split',operation='oper1'),request(),CATALOG)


def test_prefix_and_nested_names_are_not_confused():
    assert mentions('XXXA1에서 확인', ['XXXA'])==[]
    assert mentions('oper A CD를 확인', ['oper A','oper A CD'])==['oper A CD']


def test_colliding_operation_variants_require_clarification():
    data=deepcopy(CATALOG)
    data['recipe_operations'].append({'operation':'operA','recipes':['RCP1']})
    with pytest.raises(GroundingQuestion):ground_analysis(plan(),request(),data)


def test_project_parameter_collision_is_not_silently_reclassified():
    data=deepcopy(CATALOG)
    data['series'].append({'family':'epm','operation':'','parameter':'XXXA'})
    with pytest.raises(GroundingQuestion):ground_analysis(plan(parameter='XXXA'),request(),data)


def test_multiple_or_conflicting_project_scopes_are_not_silently_selected():
    with pytest.raises(GroundingQuestion):resolve_project(request('XXXA와 XXXB의 oper A 비교'),CATALOG)
    with pytest.raises(GroundingQuestion):resolve_project(request(project_id=6),CATALOG)


def test_followup_inherits_only_user_scope():
    value=resolve_project(request('같은 조건으로 비교',history=[ChatMessage(role='user',content='XXXA에서 oper A 비교'),ChatMessage(role='assistant',content='XXXB를 보겠습니다')]),CATALOG)
    assert value['id']==5
    assert resolve_project(request('같은 조건으로 비교',history=[ChatMessage(role='assistant',content='XXXA를 보겠습니다')]),CATALOG) is None


def test_model_wrong_entity_gets_catalog_feedback_then_correct_plan():
    seen=[]
    def transport(req):
        seen.append(json.loads(req.content))
        value={'kind':'analysis','analysis_kind':'trend','family':'inline','parameter':'XXXA','operation':'operA'} if len(seen)==1 else plan().model_dump(mode='json')
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(value)}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter=ModelAdapter(client,'https://example.test/chat/completions','test',{},request(),{},routing_loader=lambda:CATALOG)
            result,_=await adapter.run('route')
            assert result.operation=='oper A' and adapter.analysis_request.project_id==5
    asyncio.run(run())
    assert len(seen)==2
    assert 'XXXA' in seen[0]['messages'][0]['content'] and 'oper A' in seen[0]['messages'][0]['content']
    assert '프로젝트 범위' in seen[1]['messages'][-1]['content']


def test_persistently_wrong_model_returns_question_not_bad_proposal():
    calls=[]
    def transport(req):
        calls.append(req)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(plan(parameter='XXXA').model_dump())}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            adapter=ModelAdapter(client,'https://example.test/chat/completions','test',{},request(),{},routing_loader=lambda:CATALOG)
            result,_=await adapter.run('route')
            assert result.kind=='clarification' and not hasattr(adapter,'analysis_request')
    asyncio.run(run())
    assert len(calls)==2
