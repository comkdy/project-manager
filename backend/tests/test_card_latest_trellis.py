"""Latest-card resolution and Trellis aggregation against disposable demo definitions."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, settings
from app.models import Dashboard, DatasetVersion
from app.rendering import draw_chart


@pytest.fixture(scope="module")
def api():
    if not settings.demo_mode or not settings.app_database_url:
        pytest.skip("Requires local demo PostgreSQL")
    from app.main import app
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "auth_mode", "local_admin")
        with TestClient(app, client=("127.0.0.1", 50015)) as client:
            yield client


def definition(factor=1):
    return {"source_id":"inline_measurements", "raw_sql":f"SELECT id, equipment_id, project_id, value * {factor} AS value FROM public.inline_measurements"}


@pytest.fixture
def dataset(api):
    response=api.post('/api/datasets',json={'name':'Trellis QA '+uuid.uuid4().hex[:8],'spec':definition()})
    assert response.status_code==201,response.text
    value=response.json()
    try:
        yield value
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==value['id']))


def card(dataset, **changes):
    return dict(dict(id='latest-trellis',title='Trellis QA',dataset_id=dataset['id'],dataset_version=1,
                     kind='bar',x='equipment_id',y='value',aggregate='avg'),**changes)


def query(api, value):
    result=api.post('/api/cards/query',json={'card':value})
    assert result.status_code==200,result.text
    return result.json()


def test_saved_cards_follow_latest_without_reusing_old_cache(api,dataset,monkeypatch):
    value=card(dataset,kind='kpi',x=None)
    first=query(api,value)
    assert query(api,value)['meta']['cached']
    monkeypatch.setattr('app.main.clear_cache',lambda:None)
    response=api.post(f"/api/datasets/{dataset['id']}/versions",json={'name':dataset['name'],'expected_version':1,'spec':definition(2)})
    assert response.status_code==201,response.text
    second=query(api,value)
    assert second['meta']['dataset_version']==2 and not second['meta']['cached']
    assert second['rows'][0]['value']==pytest.approx(first['rows'][0]['value']*2)
    assert api.get(f"/api/datasets/{dataset['id']}/latest").json()['version']==2
    # Explicit historical data browsing remains available.
    old=api.post('/api/query',json={'dataset_id':dataset['id'],'dataset_version':1,'context':{'limit':1}}).json()
    new=api.post('/api/query',json={'dataset_id':dataset['id'],'dataset_version':2,'context':{'limit':1}}).json()
    assert new['rows'][0]['value']==pytest.approx(old['rows'][0]['value']*2)
    board_id='qa_latest_'+uuid.uuid4().hex[:10]
    with SessionLocal.begin() as db:
        db.add(Dashboard(id=board_id,version=1,cards=[value],layout=[{'i':value['id'],'x':0,'y':0,'w':4,'h':4}]))
    try:
        from app.main import dashboard_json
        with SessionLocal() as db:
            board=db.get(Dashboard,board_id)
            shown=dashboard_json(board,db)
            assert shown['cards'][0]['dataset_version']==2
            assert board.cards[0]['dataset_version']==1
        saved=api.put(f'/api/dashboards/{board_id}',json={'version':1,'cards':[value],'layout':shown['layout']})
        assert saved.status_code==200,saved.text
        assert saved.json()['cards'][0]['dataset_version']==2
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Dashboard).where(Dashboard.id==board_id))


def test_removed_latest_column_is_reported_instead_of_using_old_data(api,dataset):
    spec=definition();spec['raw_sql']='SELECT id, equipment_id, project_id FROM public.inline_measurements'
    response=api.post(f"/api/datasets/{dataset['id']}/versions",json={'name':dataset['name'],'expected_version':1,'spec':spec})
    assert response.status_code==201,response.text
    response=api.post('/api/cards/query',json={'card':card(dataset)})
    assert response.status_code==422 and '최신 데이터셋' in response.json()['detail']


@pytest.mark.parametrize('kind',['bar','line','scatter','histogram','box'])
def test_trellis_groups_before_aggregation_and_preserves_counts(api,dataset,kind):
    value=card(dataset,kind=kind,trellis='project_id',aggregate='count' if kind in {'bar','line','histogram'} else 'none',
               x='value' if kind=='histogram' else 'equipment_id')
    result=query(api,value)
    panels={r['trellis'] for r in result['rows']}
    assert len(panels)==3
    plain=query(api,{**value,'trellis':None})
    if kind in {'bar','line','histogram'}:
        assert sum(r['y'] for r in result['rows'])==sum(r['y'] for r in plain['rows'])
        if kind=='histogram':
            assert {r['x'] for r in result['rows']}.issubset({r['x'] for r in plain['rows']})
    else:
        assert len(result['rows'])==len(plain['rows'])
    assert result['meta']['trellis']=='project_id'
    svg=draw_chart({'card':value,'result':result},'svg').decode()
    assert '<svg' in svg and '3 panels' in svg


def test_expression_is_computed_within_each_panel_and_limits_are_enforced(api,dataset):
    expression=[{'kind':'column','name':'value'},{'kind':'operator','value':'/'},{'kind':'number','value':'2'}]
    value=card(dataset,trellis='project_id',y=None,value_expression=expression,value_expression_name='Half')
    result=query(api,value)
    normal=query(api,card(dataset,trellis='project_id'))
    expected={(r['x'],r['trellis']):r['y']/2 for r in normal['rows']}
    assert all(r['y']==pytest.approx(expected[(r['x'],r['trellis'])]) for r in result['rows'])
    for changed in ({'trellis':'id'},{'trellis':'missing'},{'kind':'kpi'},{'kind':'wafer'}):
        response=api.post('/api/cards/query',json={'card':{**value,**changed}})
        assert response.status_code==422,response.text
    assert query(api,{**value,'trellis':'id','filters':[{'column':'id','op':'lte','value':3}]})['meta']['returned_rows']<=3


def test_trellis_export_shares_log_axes_and_keeps_null_panel(monkeypatch):
    from matplotlib.figure import Figure
    from app.schemas import CardSpec
    saved=CardSpec(**card({'id':'test'},kind='scatter',aggregate='none',trellis='group',
                         options={'x_scale':'log','y_scale':'log','x_min':1,'x_max':1000,'y_min':1,'y_max':1000}))
    saved=CardSpec.model_validate_json(saved.model_dump_json())
    assert saved.trellis=='group'
    observed=[]
    original=Figure.savefig
    def capture(figure,*args,**kwargs):
        observed.extend((ax.get_title(),ax.get_xscale(),ax.get_yscale(),ax.get_xlim(),ax.get_ylim()) for ax in figure.axes if ax.get_visible())
        return original(figure,*args,**kwargs)
    monkeypatch.setattr(Figure,'savefig',capture)
    rows=[{'x':1,'y':10,'trellis':'A'},{'x':100,'y':1000,'trellis':'B'},{'x':10,'y':100,'trellis':None}]
    svg=draw_chart({'card':saved.model_dump(),'result':{'rows':rows}},'svg').decode()
    assert '(빈 값)' in svg and len(observed)==3
    assert all(x=='log' and y=='log' and xlim==(1,1000) and ylim==(1,1000) for _,x,y,xlim,ylim in observed)
