"""Grounded explanations, unchanged actions, and revocation-safe history."""
import asyncio
from copy import deepcopy
import json

import pytest

from app import evaluation_chat as chat, evaluation_narrative as narrative, chat_workflow
from app.assistant import ChatRequest
from app.chat_contracts import OUTPUTS
from app import chat_history as history
from test_chat_history import env, start, finish
from test_evaluation_chat import CASE, result


def model_plan(payload):
    fact=next(f for f in payload['facts'] if f['field']=='inline_observations')
    citation={'evidence_id':fact['id']}
    return {'kind':'evaluation_explanation',
        'findings':[{'text':'등록된 사례에서는 Inline alpha가 증가했습니다. EPM 변화와 함께 볼 관측이며 원인은 아직 확인되지 않았습니다.','citations':[citation]}],
        'next_checks':[{'text':'다음에는 기간을 나누어 이 관측이 반복되는지 확인하는 것이 좋겠습니다. 아직 실행한 비교는 아닙니다.','citations':[citation]}],
        'caveats':[{'text':'이 관측만으로 인과관계를 확정할 수는 없습니다.','citations':[citation]}]}


def answer_with_narrative():
    value=chat.answer_from(result(),{'query':'평가 사례 설명','project_id':None,'dataset_id':'epm','card':None})
    payload=narrative.evidence_payload(value)
    return narrative.attach_narrative(value,narrative.Narrative.model_validate(model_plan(payload)),payload)


@pytest.mark.parametrize('invalid',['reference','quote','number','causal','link','consistent_trend'])
def test_unbacked_claims_are_rejected(invalid):
    value=chat.answer_from(result(),{'query':'사례'})
    payload=narrative.evidence_payload(value); plan=model_plan(payload)
    statement=plan['findings'][0]
    if invalid=='reference':statement['citations'][0]['evidence_id']='missing'
    if invalid=='quote':statement['citations'][0]['quote']='없는 관측 내용'
    if invalid=='number':statement['text']='예측 정확도가 99.99%입니다.'
    if invalid=='causal':statement['text']='Recipe가 확실한 원인입니다. 인과관계가 입증되었습니다.'
    if invalid=='link':statement['text']='http://untrusted.invalid 에서 확인하세요.'
    if invalid=='consistent_trend':statement['text']='Recipe별 평균값 사이에 일관된 경향성이 나타났습니다.'
    with pytest.raises(ValueError):narrative.validate_narrative(narrative.Narrative.model_validate(plan),payload)


def test_repair_feedback_identifies_all_unbacked_paragraphs():
    payload=narrative.evidence_payload(chat.answer_from(result(),{})); plan=model_plan(payload)
    plan['findings'][0]['text']='오차는 99.99입니다.'
    plan['next_checks'][0]['citations'][0]['evidence_id']='missing'
    with pytest.raises(ValueError) as caught:
        narrative.validate_narrative(narrative.Narrative.model_validate(plan),payload)
    assert '1번째' in str(caught.value) and '99.99' in str(caught.value) and '2번째' in str(caught.value)


@pytest.mark.parametrize('text',[
    '인과관계가 확정된 것은 아닙니다.',
    '원인으로 확정한 결과가 아닌 관측입니다.',
    '인과관계가 입증된 결과가 아님을 유의하세요.',
])
def test_korean_causal_negations_are_not_false_positive(text):
    payload=narrative.evidence_payload(chat.answer_from(result(),{})); plan=model_plan(payload)
    plan['caveats'][0]['text']=text
    narrative.validate_narrative(narrative.Narrative.model_validate(plan),payload)


def test_computed_evidence_and_scope_are_passed_without_raw_rows_or_sql():
    case=deepcopy(CASE)
    case['details']['analysis_result']='Inline: 오차 증가 0.04. Recipe: 오차 증가 0.002. 예측 관계입니다.'
    case['execution_evidence']={'run_id':'r1','summary':case['details']['analysis_result'],'captured_at':'2026-10-25','dataset_version':1}
    case['raw_rows']=[{'secret':1}]
    case['spec']={'raw_sql':'SELECT hidden'}
    value=chat.answer_from(result([case]),{'query':'중요한 항목','period_start':{'year':2026,'week':40}})
    payload=narrative.evidence_payload(value)
    assert payload['sources'][0]['kind']=='approved_computation'
    assert payload['scope']['period_start']['week']==40
    assert 'raw_rows' not in json.dumps(payload) and 'SELECT hidden' not in json.dumps(payload)
    plan=model_plan(payload)
    fact=next(f for f in payload['facts'] if f['field']=='analysis_result')
    plan['findings']=[{'text':'이 모델에서 Inline을 섞었을 때 오차 증가 0.04, Recipe는 0.002로 관측됐습니다.','citations':[{'evidence_id':fact['id']}]}]
    narrative.validate_narrative(narrative.Narrative.model_validate(plan),payload)
    case['execution_evidence']['unavailable']=True
    assert not narrative.evidence_payload(chat.answer_from(result([case]),{}))['facts']


def test_computed_inline_explanation_requires_operation_and_parameter():
    case = deepcopy(CASE)
    case['execution_evidence'] = {'run_id':'r1', 'measurement_items': {
        'labels': {'inline_main':'Inline oper B CD / MAIN'},
        'items': [{'family':'inline','operation':'oper B CD','parameter':'MAIN','label':'Inline oper B CD / MAIN'}]}}
    payload = narrative.evidence_payload(chat.answer_from(result([case]), {}))
    plan = model_plan(payload)
    for bad in ('Inline MAIN과의 관계입니다.', 'Inline oper B CD와의 관계입니다.', 'MAIN과의 관계입니다.'):
        plan['findings'][0]['text'] = bad
        with pytest.raises(ValueError, match='operation\\+parameter'):
            narrative.validate_narrative(narrative.Narrative.model_validate(plan), payload)
    plan['findings'][0]['text'] = 'Inline oper B CD / MAIN과의 관측 관계이며 원인은 확인되지 않았습니다.'
    narrative.validate_narrative(narrative.Narrative.model_validate(plan), payload)
    assert any(f['field']=='measurement_identity' for f in payload['facts'])
    payload['facts'].append({**payload['facts'][0], 'id':'old', 'case_ref':{'id':'reported','version':1}})
    plan['findings'][0]['citations'] = [{'evidence_id':'old'}]
    plan['findings'][0]['text'] = 'Inline MAIN 평균에 차이가 있습니다.'
    with pytest.raises(ValueError, match='공정 미확인'):
        narrative.validate_narrative(narrative.Narrative.model_validate(plan), payload)
    plan['findings'][0]['text'] = 'Inline MAIN은 측정 공정 미확인으로 현재 계산 항목과 동일하다고 볼 수 없습니다.'
    narrative.validate_narrative(narrative.Narrative.model_validate(plan), payload)


def test_unsupported_written_ratio_is_rejected():
    payload = narrative.evidence_payload(chat.answer_from(result(), {}))
    plan = model_plan(payload)
    plan['findings'][0]['text'] = 'Inline의 기여도가 수십 배 큽니다.'
    with pytest.raises(ValueError, match='배수'):
        narrative.validate_narrative(narrative.Narrative.model_validate(plan), payload)


def test_citation_text_is_attached_from_unchanged_server_evidence():
    case=deepcopy(CASE)
    case['details']['inline_observations']='Inline alpha 증가.\n' + ('긴 근거 원문 ' * 400)
    answer=chat.answer_from(result([case]),{})
    payload=narrative.evidence_payload(answer)
    excerpts=[f for f in payload['facts'] if f['field']=='inline_observations']
    assert len(excerpts)>2
    assert all(len(f['text'])<=1400 and f['text'] in case['details']['inline_observations'] for f in excerpts)
    output=narrative.attach_narrative(answer,narrative.Narrative.model_validate(model_plan(payload)),payload)
    assert output['narrative']['findings'][0]['citations'][0]['quote']==excerpts[0]['text']


def test_private_history_restores_explanation_only_with_unchanged_approved_evidence(env,monkeypatch):
    current=result()
    monkeypatch.setattr(chat,'retrieve',lambda _:deepcopy(current))
    answer=answer_with_narrative(); c=start(env,dataset_id='epm')
    finish(c,'evaluation_cases',answer)
    with env.sessions() as db:
        stored=db.get(history.Turn,(c['id'],1)).response
        assert stored['narrative_record']['basis']
        assert 'cases' not in stored and 'facts' not in stored
    restored=env.client.get('/api/chat/conversations/'+c['id']).json()['messages'][-1]['evaluation_cases']
    assert restored['narrative']['status']=='ready'
    assert restored['narrative']['findings'][0]['citations'][0]['case_ref']=={'id':'case-a','version':2}
    current['cases']=[]
    withdrawn=env.client.get('/api/chat/conversations/'+c['id']).json()['messages'][-1]
    assert withdrawn['evaluation_cases']['narrative']['status']=='unavailable'
    assert 'Inline alpha가 증가' not in json.dumps(withdrawn,ensure_ascii=False)
    _,messages=history.begin_turn(ChatRequest(message='첫 번째 제안으로 카드를 만들어줘',dataset_id='epm',conversation_id=c['id'],conversation_revision=1),'owner')
    assert 'Inline alpha가 증가' not in json.dumps(messages,ensure_ascii=False)


def test_changed_scope_or_evidence_does_not_replay_old_interpretation():
    original=answer_with_narrative()
    for change in ('scope','text','version'):
        value=deepcopy(original)
        value.pop('narrative'); value.pop('_narrative_record')
        if change=='scope':value['search']['project_id']=7
        if change=='text':value['cases'][0]['details']['inline_observations']='alpha 감소'
        if change=='version':value['cases'][0]['version']=3
        narrative.restore_narrative(value,original['_narrative_record'])
        assert value['narrative']['status']=='unavailable' and 'findings' not in value['narrative']


def test_failed_explanation_notice_survives_saved_chat(monkeypatch):
    monkeypatch.setattr(chat,'retrieve',lambda _:result())
    answer=chat.answer_from(result(),{'query':'설명','project_id':None})
    answer['narrative']={'status':'unavailable','message':narrative.UNAVAILABLE}
    restored=chat.restore_evaluation_answer(chat.compact_evaluation_answer(answer),None)
    assert restored['narrative']['message']==narrative.UNAVAILABLE and restored['cases']


@pytest.mark.parametrize('mode',['ready','provider_error','withdrawn','empty'])
def test_explanation_workflow_rechecks_sources_and_never_loads_source_catalog(monkeypatch,mode):
    current=result([] if mode=='empty' else None); calls=[]
    monkeypatch.setattr(chat,'retrieve',lambda _:deepcopy(current))
    class Adapter:
        async def run(self,skill,extra=None,validate=None):
            calls.append(skill)
            if skill=='route':return OUTPUTS['route'].validate_python({'kind':'evaluation_cases'}),None
            assert skill=='evaluation_explanation'
            if mode=='provider_error':raise ValueError('provider unavailable')
            plan=narrative.Narrative.model_validate(model_plan(extra['evidence']))
            validate(plan)
            if mode=='withdrawn':current['cases']=[]
            return plan,None
        async def load_context(self):raise AssertionError('No SQL/card context for explanations')
    async def collect():return dict([v async for v in chat_workflow.run_workflow(ChatRequest(message='어떤 항목이 중요해?'),Adapter())])
    events=asyncio.run(collect()); answer=events['evaluation_cases']
    assert not {'proposal','analysis','error'} & events.keys()
    if mode=='empty':assert calls==['route'] and not answer.get('narrative')
    elif mode=='ready':assert answer['narrative']['status']=='ready'
    else:assert answer['narrative']['status']=='unavailable'
    if mode=='withdrawn':assert not answer['cases'] and not answer['suggestions']


def test_followup_context_contains_verified_next_checks_but_not_new_approved_facts(env,monkeypatch):
    monkeypatch.setattr(chat,'retrieve',lambda _:result())
    c=start(env,dataset_id='epm'); finish(c,'evaluation_cases',answer_with_narrative())
    _,messages=history.begin_turn(ChatRequest(message='그 기간별 비교 카드를 만들어줘',dataset_id='epm',conversation_id=c['id'],conversation_revision=1),'owner')
    text=json.dumps(messages,ensure_ascii=False)
    assert '다음에는 기간을 나누어' in text and '새 승인 사실이 아님' in text


@pytest.mark.parametrize('host,skill,bounded',[
    ('openrouter.ai','evaluation_explanation',True),
    ('internal.example','evaluation_explanation',False),
    ('openrouter.ai','query',False),
])
def test_explanation_reserves_provider_budget_without_changing_query_or_internal_server(host,skill,bounded):
    import httpx
    payload=narrative.evidence_payload(chat.answer_from(result(),{}))
    seen=[]
    def respond(request):
        seen.append(json.loads(request.content))
        plan=model_plan(payload) if skill=='evaluation_explanation' else {'kind':'clarification','question':'어떤 기간을 볼까요?','options':[]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(plan)}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter=chat_workflow.ModelAdapter(client,f'https://{host}/api/v1/chat/completions','test-model',{},ChatRequest(message='설명해줘'),{})
            await adapter.run(skill,{'evidence':payload})
    asyncio.run(run())
    assert len(seen)==1 and seen[0]['max_tokens']==5000
    if bounded:
        assert seen[0]['reasoning']=={'effort':'low'}
        assert seen[0]['response_format']=={'type':'json_object'}
    else:
        assert 'reasoning' not in seen[0] and 'response_format' not in seen[0]
