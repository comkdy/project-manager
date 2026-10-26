import uuid
from copy import deepcopy

from sqlalchemy import delete

from app.db import SessionLocal
from app.models import Audit, Dashboard, DatasetVersion
from app.query import clear_cache
from test_chat_workflow import api, bundle, counts, events, mock_llm, needs_dataset, query, SQL
from test_integration import dataset


def test_python_card_preview_period_filters_and_failure_isolation(api, monkeypatch):
    ds=dataset(api,'inline_measurements')
    card={'id':'python-qa','title':'Script', 'kind':'scatter','dataset_id':ds['id'],'dataset_version':ds['version'],
          'editor_mode':'python','python_script':'require(df["wafer_id"].min() == 1 and df["wafer_id"].max() == 1 or df.empty, "filter failed")\nfig=px.violin(df,x="metric_name",y="value")',
          'period':{'use_dashboard':False},'filters':[{'column':'wafer_id','op':'eq','value':1}]}
    result=api.post('/api/cards/query',json={'card':card,'context':{'project_id':1}})
    assert result.status_code==200,result.text
    result=result.json()
    assert result['figure']['data'][0]['type']=='violin'
    assert result['rows'] == [] and result['meta']['input_rows'] > 0 and result['meta']['period'] is None
    card['period']={'use_dashboard':True}
    future=api.post('/api/cards/query',json={'card':card,'context':{'project_id':1,'period_start':{'year':9998,'week':1}}})
    assert future.status_code==200, future.text
    assert future.json()['rows']==[] and future.json()['meta']['input_rows']==0
    card['python_script']='df.to_csv("outside.csv")'
    assert api.post('/api/cards/query',json={'card':card}).status_code==422
    card['python_script']='import pandas as pd\nfig=go.Figure()'
    forbidden=api.post('/api/cards/query',json={'card':card})
    assert forbidden.status_code==422 and 'import는 사용할 수 없습니다' in forbidden.text
    assert api.post('/api/cards/python-script',json={'card':card,'context':{'project_id':1}}).status_code==422
    card['python_script']='fig=px.pie(df,names="metric_name",values="value")'
    card['period']={'use_dashboard':False}
    assert api.post('/api/cards/query',json={'card':card}).status_code==200


def test_ai_python_card_reuses_existing_dataset_and_skips_creation(api,monkeypatch):
    ds=next(d for d in api.get('/api/bootstrap?project_id=1').json()['datasets']
            if d['spec']['source_id']=='inline_measurements' and d['kind']=='source')
    proposed={'id':'new','title':'Violin','kind':'box','dataset_id':ds['id'],'dataset_version':ds['version'],
              'editor_mode':'python','script_input':'dataset','python_script':'fig=px.violin(df,x="metric_name",y="value")',
              'period':{'use_dashboard':False},'filters':[{'column':'wafer_id','op':'eq','value':1}]}
    observed=mock_llm(monkeypatch,[{'kind':'route','intent':'card'}, {'kind':'card','explanation':'기존 데이터셋 재사용','card':proposed}])
    before=counts()
    response=events(api.post('/api/chat',json={'message':'기존 데이터로 바이올린 차트','project_id':1}))
    assert 'error' not in response,response
    p=response['proposal']
    assert 'dataset' not in p and p['card_result']['figure']['data'][0]['type']=='violin'
    assert counts()==before and len(observed)==2
    assert '카드 디자인 지침' in observed[-1]['messages'][0]['content']


def test_existing_shared_card_remains_editable_with_project_execution_scope(api, monkeypatch):
    ds=dataset(api,'inline_measurements')
    card={'id':'legacy-common-card','title':'Existing card','kind':'box','dataset_id':ds['id'],
          'dataset_version':ds['version'],'editor_mode':'python','script_input':'dataset',
          'python_script':'require(df["project_id"].min() == 1 and df["project_id"].max() == 1, "project boundary failed")\nfig=px.violin(df,x="metric_name",y="value")',
          'period':{'use_dashboard':False},'filters':[]}
    mock_llm(monkeypatch,[{'kind':'route','intent':'card'}, {'kind':'card','explanation':'기존 카드 수정','card':card}])
    response=events(api.post('/api/chat',json={'message':'기존 카드 표시를 수정해줘','project_id':1,'dataset_id':ds['id'],'card':card}))
    assert 'error' not in response,response
    assert response['proposal']['card_result']['meta']['input_rows']>0
    assert response['proposal']['card']['dataset_id']==ds['id']


def test_ai_legacy_chart_script_is_migrated_before_display_and_preview(api, monkeypatch):
    from app.plotly_codegen import simple_script
    ds = next(d for d in api.get('/api/bootstrap?project_id=1').json()['datasets']
              if d['spec']['source_id']=='inline_measurements' and d['kind']=='source')
    proposed = {'id': 'legacy-ai', 'title': 'Legacy means', 'kind': 'bar',
                'dataset_id': ds['id'], 'dataset_version': ds['version'],
                'x': 'metric_name', 'y': 'value', 'aggregate': 'avg',
                'editor_mode': 'python', 'script_input': 'chart',
                'period': {'use_dashboard': False},
                'filters': [{'column': 'wafer_id', 'op': 'eq', 'value': 1}]}
    proposed['python_script'] = simple_script(proposed) + '\nfig.update_traces(opacity=0.42)'
    mock_llm(monkeypatch, [{'kind': 'route', 'intent': 'card'},
                         {'kind': 'card', 'explanation': '기존 데이터셋 사용', 'card': proposed}])
    before = counts()
    result = events(api.post('/api/chat', json={'message': '기존 평균 카드의 투명도를 낮춰줘', 'project_id': 1}))
    assert 'error' not in result, result
    proposal = result['proposal']
    assert 'dataset' not in proposal and counts() == before
    card = proposal['card']
    assert card['script_input'] == 'dataset'
    assert 'groupby' in card['python_script'] and 'metric_name' in card['python_script']
    assert card['python_script'].endswith('fig.update_traces(opacity=0.42)')
    preview = proposal['card_result']
    assert preview['figure']['data'][0]['opacity'] == 0.42
    assert preview['rows'] == [] and preview['meta']['input_rows'] > 0
    replay = api.post('/api/cards/query', json={'card': card, 'context': {'project_id': 1}})
    assert replay.status_code == 200, replay.text
    assert replay.json()['figure']['data'] == preview['figure']['data']


def test_new_dataset_request_requires_catalog_review(api,monkeypatch):
    observed=mock_llm(monkeypatch,[{'kind':'route','intent':'card'},
        {'kind':'needs_dataset','query_request':'새 데이터'},
        {'kind':'clarification','question':'어떤 측정값을 비교할까요?'}])
    result=events(api.post('/api/chat',json={'message':'비교 차트','project_id':1}))
    assert 'clarification' in result and 'proposal' not in result
    assert 'reviewed_dataset_ids' in observed[-1]['messages'][-1]['content']


def test_identical_query_is_reused_before_dataset_skill_and_during_apply(api,monkeypatch):
    created=api.post('/api/datasets',json={'name':'QA reusable','project_id':1,
                                         'spec':{'source_id':'inline_measurements','raw_sql':SQL}}).json()
    board=api.get('/api/dashboards?project_id=1').json()
    action_id=str(uuid.uuid4())
    card={'id':'qa-reused','title':'Means','kind':'bar','dataset_id':created['id'],'dataset_version':1,
          'x':'lot_code','y':'mean_value','aggregate':'none'}
    try:
        before=counts()
        observed=mock_llm(monkeypatch,[{'kind':'route','intent':'card'},needs_dataset(api,'평균 조회'),
            query(),{'kind':'card','explanation':'재사용','card':card}])
        result=events(api.post('/api/chat',json={'message':'평균 카드','project_id':1}))
        assert 'error' not in result,result
        assert 'dataset' not in result['proposal'] and result['proposal']['steps']==['card','query','reuse_dataset']
        assert len(observed)==4 and counts()==before
        # A direct/racing proposal cannot bypass duplicate prevention on apply.
        body=bundle(api)
        body['proposal']['card'].update(editor_mode='python',script_input='dataset',
            python_script='fig=px.bar(df,x="lot_code",y="mean_value")')
        saved=api.post('/api/chat/apply',json={**body,'request_id':action_id,'dashboard_id':board['id'],'dashboard_version':board['version']})
        assert saved.status_code==200,saved.text
        assert saved.json()['dataset_reused'] and saved.json()['dataset_id']==created['id'] and counts()==before
        reopened=api.get('/api/dashboards?project_id=1').json()
        stored=next(c for c in reopened['cards'] if c['id']=='new')
        assert stored['python_script']==body['proposal']['card']['python_script']
        assert stored['dataset_id']==created['id']
        checked=api.post('/api/cards/query',json={'card':stored,'context':{'project_id':1}})
        assert checked.status_code==200 and checked.json()['figure']['data'][0]['type']=='bar'
    finally:
        with SessionLocal.begin() as db:
            item=db.get(Dashboard,board['id'])
            item.cards,item.layout,item.version=board['cards'],board['layout'],board['version']
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==created['id']))
            db.execute(delete(Audit).where(Audit.entity_id.in_([created['id'],action_id])))
        clear_cache()
