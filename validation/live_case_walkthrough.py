"""One user-authorized walkthrough on the active app. Never writes source data."""
import json
from pathlib import Path
import sys
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'validation/live-case-state.json'
BASE='http://127.0.0.1:8000/api'

def call(path, body=None):
    request=urllib.request.Request(BASE+path,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=120) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{path}: {exc.code} '+exc.read().decode()) from None

def save(state):
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')

def load():
    return json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}

SQL="""WITH il AS (
 SELECT *, row_number() OVER (PARTITION BY lot_cd,lot_id,wf_id,die_x,die_y,operation,parameter
 ORDER BY end_dt_tm DESC,record_id DESC) rn
 FROM public.inline_data WHERE lot_cd='XXXA' AND operation='oper B CD' AND parameter='MAIN'
), ep AS (
 SELECT *, row_number() OVER (PARTITION BY lot_cd,lot_id,wf_id,die_x,die_y,parameter
 ORDER BY end_dt_tm DESC,record_id DESC) rn
 FROM public.epm_data WHERE lot_cd='XXXA' AND parameter='PARA3'
), his AS (
 SELECT *, row_number() OVER (PARTITION BY lot_cd,lot_id,wf_id,operation
 ORDER BY end_dt_tm DESC,record_id DESC) rn
 FROM public.wafer_his WHERE lot_cd='XXXA' AND operation='oper B'
), paired AS (
 SELECT ep.lot_cd,ep.lot_id,ep.wf_id,his.recipe AS recipe_b,
 avg(il.value) AS inline_main,avg(ep.value) AS epm_para3,
 count(*) AS paired_sites,max(ep.end_dt_tm) AS epm_at,
 max(il.end_dt_tm) AS inline_at,max(his.end_dt_tm) AS recipe_at
 FROM ep JOIN il ON (ep.lot_cd,ep.lot_id,ep.wf_id,ep.die_x,ep.die_y)=(il.lot_cd,il.lot_id,il.wf_id,il.die_x,il.die_y)
 JOIN his ON (ep.lot_cd,ep.lot_id,ep.wf_id)=(his.lot_cd,his.lot_id,his.wf_id)
 WHERE ep.rn=1 AND il.rn=1 AND his.rn=1 AND ep.value IS NOT NULL AND il.value IS NOT NULL
 AND his.end_dt_tm<=il.end_dt_tm AND il.end_dt_tm<ep.end_dt_tm
 GROUP BY ep.lot_cd,ep.lot_id,ep.wf_id,his.recipe
)
"""

def inspect():
    state=load()
    bootstrap=call('/bootstrap')
    project=next(p for p in bootstrap['projects'] if p['code']=='XXXA')
    dataset=next(d for d in bootstrap['datasets'] if d['name']=='epm_data')
    def preview(sql):
        return call('/datasets/preview',{'spec':{'source_id':dataset['spec']['source_id'],'raw_sql':sql},'context':{'project_id':project['id'],'limit':100}})['rows']
    state.update(project_id=project['id'],baseline_sql=SQL)
    state['baseline']=preview(SQL+'''SELECT count(*) AS wafers,count(DISTINCT lot_id) AS lots,
       sum(paired_sites) AS paired_sites,min(paired_sites) AS min_sites,max(paired_sites) AS max_sites,
       min(epm_at) AS first_epm,max(epm_at) AS last_epm,
       avg(inline_main) AS inline_mean,avg(epm_para3) AS epm_mean,
       corr(inline_main,epm_para3) AS pearson_r,
       count(*) FILTER (WHERE recipe_at>inline_at OR inline_at>=epm_at) AS invalid_order FROM paired''')
    state['groups']=preview(SQL+'''SELECT recipe_b,count(*) AS wafers,avg(inline_main) AS inline_mean,
       avg(epm_para3) AS epm_mean FROM paired GROUP BY recipe_b ORDER BY recipe_b''')
    save(state)
    print(json.dumps({k:state[k] for k in ('project_id','baseline','groups')},ensure_ascii=False))


def register():
    state=load()
    if state.get('case_question_id'):
        raise RuntimeError('The walkthrough case is already registered; continue it instead.')
    text='''[현재 DB 실증 · XXXA] oper B Recipe·Inline MAIN과 EPM PARA3의 관계
2026-10-25에 현재 연결된 wafer_fab_demo의 XXXA 데이터를 읽기 전용으로 사전 확인했다. 이 DB는 합성 공정 데이터이며 실제 사내 실험이나 인과 효과를 뜻하지 않는다.
oper B Recipe를 RCP1/RCP2/RCP3로 구분했을 때 Inline MAIN 평균은 각각 32.091824/32.136139/32.283723, EPM PARA3 평균은 각각 1.417389/1.413549/1.420269였다. 표본은 각각 81/74/88 wafer다. Recipe 번호는 연속 수치가 아닌 범주 이름이다. Recipe별 Inline 차이는 보이지만 EPM의 단조 변화나 Recipe의 인과 효과는 확인하지 못했다.
전체 20 Lot·243 wafer에서 wafer별 Inline MAIN과 EPM PARA3 평균의 Pearson 상관은 0.982314였다. 두 값이 같이 움직이는 관측 관계이며 Lot·wafer 편차와 Recipe가 함께 얽혀 있을 수 있다. EPM 결과 시점은 UTC 2026-08-25T00:39:07부터 2026-10-19T00:59:07까지다.
이번 사례에서 oper B Recipe는 wafer_his의 operation='oper B'인 recipe 값이다. Inline MAIN은 inline_data의 operation='oper B CD', parameter='MAIN'인 value이고, EPM PARA3는 epm_data의 parameter='PARA3'인 value다. 단위와 합격 기준은 확인되지 않았으므로 지정하지 않는다. 장비명 컬럼은 없어 장비 영향은 이번 분석에서 판단하지 않는다.
분석 범위는 lot_cd='XXXA'다. wafer 식별자는 (lot_cd,lot_id,wf_id), 측정 위치는 여기에 die_x,die_y를 더한다. 다른 Lot의 같은 wf_id를 같은 wafer로 연결하지 않는다. 각 테이블의 같은 식별자·공정·parameter에서 end_dt_tm 내림차순, 같은 시각이면 record_id 내림차순으로 마지막 행 한 개를 선택한다. 마지막 값이 NULL이면 과거 값으로 대체하지 않고 제외한다.
두 측정의 동일 wafer·동일 die 위치를 먼저 대응하고, oper B 공정 완료 시각 <= Inline 시각 < EPM 시각인 유효 대응만 사용한다. 그 대응 위치들의 Inline/EPM 산술평균을 계산해 wafer당 한 행으로 만든다. 유효 위치는 총 11,805개, wafer당 46~49개였고 시간 순서 위반은 0건이었다. 위치 누락으로 제외된 표본의 선택 편향 가능성은 남는다.
새 비교 데이터셋은 lot_cd,lot_id,wf_id,recipe_b,inline_main,epm_para3,epm_at을 포함하면 된다. recipe_b는 위 oper B Recipe, inline_main은 위 대응 위치의 Inline 평균, epm_para3는 같은 위치의 EPM 평균, epm_at은 해당 wafer 대응 위치들의 마지막 EPM 시각이다. 한 행은 위 조건으로 대응한 한 wafer다. recipe_b와 inline_main은 EPM 이전에 알 수 있는 항목이다.
후속 분석에서는 epm_para3를 결과로, inline_main과 recipe_b를 설명 항목으로 비교하고 싶다. ML을 쓴다면 같은 lot_id를 학습·검증 양쪽에 나누지 않고 epm_at 기준 과거 학습·이후 검증으로 확인한다. 이번 것은 전체 절차 시연을 위한 1회 분석이다. 자동 지속 분석은 켜지 않는다.'''
    result=call('/semantic/collection/evaluations',{'project_id':state['project_id'],'text':text})
    state.update(case_text=text,case_question_id=result['question_id'],case_answer_id=result['answer_id'])
    save(state)
    print(json.dumps(result,ensure_ascii=False))


def case_status():
    state=load()
    question=call('/semantic/collection/questions/'+state['case_question_id'])
    state['case_question']=question
    if question.get('draft'):
        state['case_draft']=call('/semantic/collection/drafts/'+question['draft']['id'])
    save(state)
    print(json.dumps({'question':question,'draft':state.get('case_draft')},ensure_ascii=False))


def preview_dataset():
    from copy import deepcopy
    state=load()
    original=state['dataset_recommendation']['proposal']
    proposal={key:deepcopy(original[key]) for key in ('kind','explanation','dataset','evaluation_dataset_recommendation_id')}
    proposal['dataset']['spec']['raw_sql']=SQL+'''SELECT lot_cd,lot_id,wf_id,recipe_b,inline_main,epm_para3,epm_at
FROM paired ORDER BY lot_cd,lot_id,wf_id'''
    proposal['explanation']+=' 검토 보정: 각 원천의 마지막 행을 먼저 선택하고 마지막 NULL은 제외하며, 공정 완료 <= Inline < EPM 순서를 검사한다. 같은 위치의 대응값만 wafer 평균에 포함한다.'
    state['reviewed_dataset_proposal']=proposal
    response=call('/chat/preview', {'proposal':proposal,'project_id':state['project_id'],'workspace_token':call('/bootstrap')['workspace_token']})
    state['reviewed_dataset_preview']=response
    save(state)
    print(json.dumps({'keys':list(response),'preview':{k:v for k,v in response.items() if k not in ('workspace_token','query_result','proposal')},'query_meta':response.get('query_result',{}).get('meta')},ensure_ascii=False))


def apply_dataset():
    import uuid
    state=load()
    assert state.get('reviewed_dataset_preview') and not state.get('dataset_apply')
    response=call('/chat/apply', {'proposal':state['reviewed_dataset_proposal'],'project_id':state['project_id'],'workspace_token':call('/bootstrap')['workspace_token'],'request_id':str(uuid.uuid4())})
    state['dataset_apply']=response; save(state)
    print(json.dumps(response,ensure_ascii=False))


def optimize_dataset():
    state=load()
    prefix='''WITH il AS MATERIALIZED (
 SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y) *
 FROM public.inline_data WHERE lot_cd='XXXA' AND operation='oper B CD' AND parameter='MAIN'
 ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,end_dt_tm DESC,record_id DESC
), ep AS MATERIALIZED (
 SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y) *
 FROM public.epm_data WHERE lot_cd='XXXA' AND parameter='PARA3'
 ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,end_dt_tm DESC,record_id DESC
), his AS MATERIALIZED (
 SELECT DISTINCT ON (lot_cd,lot_id,wf_id) *
 FROM public.wafer_his WHERE lot_cd='XXXA' AND operation='oper B'
 ORDER BY lot_cd,lot_id,wf_id,end_dt_tm DESC,record_id DESC
), paired AS ('''
    optimized=prefix+SQL.split('), paired AS (',1)[1].replace('ep.rn=1 AND il.rn=1 AND his.rn=1 AND ','')
    spec={**state['reviewed_dataset_proposal']['dataset']['spec'],'raw_sql':optimized+'''SELECT count(*) AS wafers,count(DISTINCT lot_id) AS lots,
sum(paired_sites) AS paired_sites,corr(inline_main,epm_para3) AS pearson_r FROM paired'''}
    result=call('/datasets/preview',{'spec':spec,'context':{'project_id':state['project_id'],'limit':10}})
    state['optimized_verification']=result['rows']
    original=state['baseline'][0]; actual=result['rows'][0]
    assert actual['wafers']==original['wafers'] and actual['lots']==original['lots'] and actual['paired_sites']==original['paired_sites']
    assert abs(actual['pearson_r']-original['pearson_r'])<1e-12
    state['optimized_dataset_spec']={**spec,'raw_sql':optimized+'SELECT lot_cd,lot_id,wf_id,recipe_b,inline_main,epm_para3,epm_at FROM paired ORDER BY lot_cd,lot_id,wf_id'}
    save(state)
    print(json.dumps({'verified':True,'result':result['rows'],'meta':result['meta']},ensure_ascii=False))


def submit_result():
    state=load(); run=state['analysis']; result=run['result']; ml=result['ml']
    assert run['status']=='completed' and result['knowledge_eligible']
    assert result['row_count']==state['baseline'][0]['wafers']==243
    inline=next(v for v in ml['statistics'] if v['column']=='inline_main')
    assert abs(inline['pearson_to_target']-state['baseline'][0]['pearson_r'])<1e-12
    assert ml['validation']['train_units']==15 and ml['validation']['test_units']==5
    assert ml['validation']['train_latest']<ml['validation']['test_earliest']
    for expected,actual in zip(state['groups'],ml['groups'][0]['values']):
        assert expected['recipe_b']==actual['value'] and expected['wafers']==actual['count']
        assert abs(expected['epm_mean']-actual['target_mean'])<1e-12
    response=call('/evaluation-analysis/'+run['id']+'/submit-review', {'expected_revision':run['revision'],
        'workspace_token':call('/bootstrap')['workspace_token'],
        'note':'현재 합성 DB의 실증 예시입니다. 원본 SQL로 확인한 20 Lot·243 wafer의 통계와 일치합니다. 학습 15 Lot·183 wafer와 이후 시점 검증 5 Lot·49 wafer를 분리했으며 경계 Lot의 학습 11행은 제외했습니다. Inline MAIN과 Recipe의 예측 관계를 관측한 결과이고, 실제 제조 인과 효과·장비 영향·합격 판정으로 사용하지 않습니다.'})
    state['result_review']=response; save(state)
    print(json.dumps(response,ensure_ascii=False))

if __name__=='__main__':
    globals()[sys.argv[1]]()
