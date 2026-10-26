"""Current DB and production worker/API; deterministic plan, no model/network egress.

The displayed validation conversations explicitly say the model was not called.
The script never approves knowledge and never changes measurement/dataset/card data.
"""
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path
from sqlalchemy import text

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app import db as database, chat_history, chat_data_analysis
from app.assistant import ChatRequest
from app.analytics.models import AnalysisRun
from app.analytics.data import analysis_connection
from app.chat_data_contracts import DataAnalysisPlan
from app.evaluation_analysis import Search,_compile
from app.database_settings import load_active_source

load_active_source()

def call(path,body=None):
    assert path.startswith('/')
    request=urllib.request.Request('http://127.0.0.1:8000/api'+path,
        data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)

project=next(p for p in call('/bootstrap')['projects'] if p['code']=='XXXA')
actor=call('/auth/me')['id']
dataset_id='aa3f3390-57a2-41b9-a185-a045dd3757c3'
plans={
 'correlation':{'method':'correlation','metrics':['inline_main','epm_para3']},
 'group_compare':{'method':'group_compare','metrics':['inline_main','epm_para3'],'group_column':'recipe_b'},
 'ml_regression':{'method':'ml_regression','metrics':['epm_para3'],'features':['inline_main','recipe_b'],
    'unit_columns':['lot_id'],'time_column':'epm_at'}}
records=[]
for method,settings in plans.items():
    message=f'[검증 · 모델 미호출] {method}: 현재 XXXA 데이터의 계산·화면·승인 연결 검증. '
    message+='각 행은 같은 lot_id, wf_id의 wafer이고 inline_main과 recipe_b는 epm_para3 이전 값입니다. lot_id 단위, epm_at 시각으로 검증합니다. 추가 필터는 없습니다.'
    request=ChatRequest(message=message,project_id=project['id'],dataset_id=dataset_id,save_history=True)
    evidence={'reference':'user','quote':message}
    roles=[(c,'target') for c in settings['metrics']]+[(c,'feature') for c in settings.get('features',[])]+[(c,'unit') for c in settings.get('unit_columns',[])]
    if settings.get('group_column'):roles.append((settings['group_column'],'group'))
    if settings.get('time_column'):roles.append((settings['time_column'],'time'))
    class DeterministicAdapter:
        actor_id=actor
        async def run(self,stage,extra=None,validate=None):
            assert stage=='data_analysis_plan'
            payload=extra['data_analysis_context']
            dataset=next(d for d in payload['datasets'] if d['id']==dataset_id)
            value=DataAnalysisPlan(kind='data_analysis_plan',dataset_id=dataset_id,dataset_version=dataset['version'],plan={
                'summary':'현재 XXXA 데이터의 관계를 계산합니다. 모델이 선택한 계획이 아닌 연결 검증용 계획입니다.',
                'settings':settings,'row_basis':evidence,'features_precede_target':method=='ml_regression',
                'columns':[{'column':c,'role':role,'reason':'검증용 명시적 분석 조건','evidence':evidence} for c,role in roles],
                'limitations':['관측·예측 관계이며 인과관계는 단정하지 않습니다.']})
            return value,validate(value)
    async def collect():return {k:v async for k,v in chat_data_analysis.run_workflow(request,DeterministicAdapter())}
    conversation,_=chat_history.begin_turn(request,actor)
    events=asyncio.run(collect())
    assert 'data_analysis' in events,events
    initial=events['data_analysis'];identity=initial['run_id']
    chat_history.finish_turn(conversation['id'],conversation['revision'],'data_analysis',initial)
    for _ in range(60):
        answer=call(f'/chat/data-analysis/{identity}?project_id={project["id"]}')
        if answer['status'] not in {'queued','running','cancelling'}:break
        time.sleep(1)
    assert answer['status']=='completed',answer
    assert answer['result']['row_count']>0 and answer['settings']['method']==method and answer['review'] is None
    record={'method':method,'model_called':False,'run_id':identity,'conversation':conversation['id'],'completed':answer}
    if method=='correlation':
        with database.SessionLocal() as db:stored=db.get(AnalysisRun,identity).request
        _,cte,params,_=_compile(Search.model_validate(stored['_evaluation_search']),check_types=True)
        with analysis_connection() as connection:
            row=connection.execute(text(cte+' SELECT count(*) n,corr(inline_main,epm_para3) r FROM filtered'),params).mappings().one()
        assert row['n']==answer['result']['row_count'] and abs(row['r']-answer['result']['correlation']['pearson'])<1e-12
        record['independent_source_check']={'rows':row['n'],'pearson':row['r']}
    if method=='ml_regression':
        ml=answer['result']['ml']
        assert ml['status'] in {'validated','not_better_than_baseline'}
        v=ml['validation']
        assert v['train_units']>=8 and v['test_units']>=3 and v['train_latest']<v['test_earliest']
        record['pending_review']=call(f'/evaluation-analysis/{identity}/submit-review',{
            'expected_revision':answer['revision'],'workspace_token':answer['workspace_token'],
            'note':'연결 검증용 실제 계산 결과입니다. 모델 분류는 호출하지 않았으며 담당자 검토 전에는 승인하지 않습니다.'})
        assert record['pending_review']['status']=='pending'
    restored=call('/chat/conversations/'+conversation['id'])['messages'][-1]['data_analysis']
    assert restored['result']==answer['result'] and restored['status']=='completed'
    record['restored']=True
    records.append(record)
    (ROOT/'validation/chat-worker-local-results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'method':method,'run_id':identity,'conversation':conversation['id'],'rows':answer['result']['row_count'],
        'explanation':answer['explanation'],'pending_review':record.get('pending_review'),'restored':True},ensure_ascii=False),flush=True)
