"""Real configured chat -> bounded worker -> pending knowledge review, never approval."""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

def call(path,body=None):
    request=urllib.request.Request('http://127.0.0.1:8000/api'+path,
        data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=240) as response:return response.read().decode()

def decode(raw):
    return {b.splitlines()[0][7:]:json.loads(b.splitlines()[1][6:])
            for b in raw.strip().split('\n\n') if b.startswith('event:')}

project=next(p for p in json.loads(call('/bootstrap'))['projects'] if p['code']=='XXXA')
dataset_id='aa3f3390-57a2-41b9-a185-a045dd3757c3'
messages={
 'correlation':'이 데이터셋에서 inline_main과 epm_para3의 상관관계를 현재 데이터로 계산하고 설명해줘. 각 행은 같은 lot_id, wf_id의 wafer 단위 대응이고 inline_main은 EPM 측정 이전 값이야. 추가 기간이나 필터 없이 데이터셋 전체 범위를 사용해줘.',
 'group_compare':'이 데이터셋에서 recipe_b별 inline_main과 epm_para3의 평균과 분산을 현재 데이터로 비교해줘. 각 행은 같은 lot_id, wf_id의 wafer야. 추가 기간이나 필터 없이 전체 범위의 각 Recipe 그룹을 비교해줘.',
 'ml_regression':'이 데이터셋에서 epm_para3를 목표로, inline_main과 recipe_b를 설명 변수로 ML 분석해줘. 두 설명 변수는 EPM 전에 알려진 값이고 행은 같은 lot_id, wf_id의 wafer에 대응돼. lot_id를 독립 검증 단위로 묶고 epm_at 측정 시각으로 과거 학습·미래 검증을 나눠줘. 추가 기간이나 필터 없이 전체 범위를 사용하고 예측에 도움되는 항목과 한계를 설명해줘.',
 'natural_ml':'XXXA에서 oper B의 Recipe와 Inline MAIN을 함께 고려하면 EPM PARA3 예측에 어떤 항목이 중요해? 관련 데이터셋과 승인된 평가 사례를 찾아 현재 데이터로 ML 분석하고 다음에 확인할 점도 알려줘. 별도 기간이나 필터는 지정하지 않을게.',
 'ambiguous':'이 데이터셋에서 장비 상태를 설명 항목으로 EPM PARA3와의 관계를 ML로 분석해줘. 장비 상태가 어느 데이터나 컬럼을 뜻하는지는 아직 정하지 않았어.'}
method=sys.argv[1] if len(sys.argv)>1 else 'correlation'
events=decode(call('/chat',{'message':messages[method],'project_id':project['id'],
    'dataset_id':None if method=='natural_ml' else dataset_id,'save_history':True}))
out=ROOT/f'validation/chat-worker-{method}-live.json'
out.write_text(json.dumps({'events':events},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'method':method,'events':list(events),'error':events.get('error'),
    'clarification':events.get('clarification'),'analysis':events.get('data_analysis',{}).get('status')},ensure_ascii=False),flush=True)
assert 'error' not in events and 'data_analysis' in events,events
initial=events['data_analysis'];identity=initial['run_id']
assert initial['dataset']['id']==dataset_id and initial['project_id']==project['id']
if method=='ambiguous':
    assert initial['status']=='needs_input' and initial['result'] is None and initial['review'] is None,initial
    assert initial['questions']
    question=json.loads(call('/semantic/collection/questions/'+initial['questions'][0]['id']))
    assert question['status']=='open' and question['analysis_setup']['run_id']==identity
    out.write_text(json.dumps({'events':events,'question':question},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'needs_input','run_id':identity,'question_id':question['id'],'question':question['title']},ensure_ascii=False))
    sys.exit(0)
for _ in range(90):
    answer=json.loads(call(f'/chat/data-analysis/{identity}?project_id={project["id"]}'))
    if answer['status'] not in {'queued','running','cancelling'}:break
    time.sleep(1)
assert answer['status']=='completed',answer
assert answer['settings']['method']==('ml_regression' if method=='natural_ml' else method) and answer['review'] is None
assert answer['result']['row_count']>0 and answer['scope']['filters']==[]
record={'events':events,'completed':answer}
# Check exact source SQL aggregation independently of chat/worker result composition.
if method=='correlation':
    from app import db as database
    from app.database_settings import load_active_source
    from app.evaluation_analysis import Search,_compile
    from app.analytics.data import analysis_connection
    from sqlalchemy import text
    load_active_source()
    with database.SessionLocal() as db:
        from app.analytics.models import AnalysisRun
        stored=db.get(AnalysisRun,identity).request
    _,cte,params,_=_compile(Search.model_validate(stored['_evaluation_search']),check_types=True)
    with analysis_connection() as connection:
        row=connection.execute(text(cte+' SELECT count(*) n, corr(inline_main,epm_para3) r FROM filtered'),params).mappings().one()
    assert row['n']==answer['result']['row_count']
    assert abs(row['r']-answer['result']['correlation']['pearson'])<1e-12
    record['independent_source_check']={'count':row['n'],'pearson':row['r']}
if method=='ml_regression':
    ml=answer['result']['ml']
    assert ml['status'] in {'validated','not_better_than_baseline'}
    assert ml['validation']['train_units']>=8 and ml['validation']['test_units']>=3
    assert ml['validation']['train_latest']<ml['validation']['test_earliest']
    pending=json.loads(call(f'/evaluation-analysis/{identity}/submit-review',{
        'expected_revision':answer['revision'],'workspace_token':answer['workspace_token'],
        'note':'챗 통계·ML 기능 검증 결과입니다. 실제 사용자 검토 전에는 승인하지 않습니다.'}))
    assert pending['status']=='pending'
    record['pending_review']=pending
restored=json.loads(call('/chat/conversations/'+events['conversation']['id']))['messages'][-1]['data_analysis']
assert restored['result']==answer['result'] and restored['status']=='completed'
record['restored']=restored
out.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'method':method,'run_id':identity,'conversation':events['conversation']['id'],
    'rows':answer['result']['row_count'],'explanation':answer['explanation'],
    'review':record.get('pending_review'),'restored':True},ensure_ascii=False),flush=True)
