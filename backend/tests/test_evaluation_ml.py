from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import numpy as np
import pytest
from fastapi import HTTPException
from app.evaluation_analysis import Settings, validate_settings
from app.evaluation_ml import calculate_ml
from app.evaluation_compute import data_fingerprint, compute
from test_evaluation_compute import source, stored

COLS=[{'name':n,'type':t} for n,t in [('inline','double precision'),('epm','double precision'),('recipe','text'),('lot','text'),('measured','timestamp with time zone')]]

def settings():
    return Settings(method='ml_regression',metrics=['epm'],features=['inline','recipe'],unit_columns=['lot'],time_column='measured')

def rows(n=300):
    rng=np.random.default_rng(54)
    return [{'inline':float(x),'recipe':'A' if i%2 else 'B','epm':float(4*x+(i%2)*8+rng.normal(0,.1)),
             'lot':f'lot{i//5:03d}','measured':datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(hours=i)}
            for i,x in enumerate(rng.uniform(-2,2,n))]

def test_ml_learns_known_relationship_on_future_disjoint_lots():
    result=calculate_ml(rows(),settings(),COLS)
    ml=result['ml'];v=ml['validation']
    assert ml['status']=='validated' and ml['metrics']['mae']<ml['metrics']['baseline_mae']*.5
    assert v['train_latest']<v['test_earliest'] and v['train_units']>=8 and v['test_units']>=3
    assert {x['column'] for x in ml['importance']}=={'inline','recipe'}
    assert ml['statistics'][0]['count']==300
    assert calculate_ml(list(reversed(rows())),settings(),COLS)['ml']['metrics']==ml['metrics']

def test_same_lot_cannot_leak_across_time_boundary():
    data=rows()
    for row in data:row['lot']='one-lot'
    result=calculate_ml(data,settings(),COLS)
    assert result['ml']['status']=='insufficient_data' and result['ml']['metrics'] is None
    assert result['ml']['validation']['train_rows']==0

def test_null_and_nonfinite_exclusions_do_not_create_confident_results():
    data=rows(20);data[0]['inline']=None;data[1]['epm']=float('inf')
    result=calculate_ml(data,settings(),COLS)
    assert result['ml']['excluded_rows']==2 and result['ml']['metrics'] is None
    assert calculate_ml([],settings(),COLS)['knowledge_eligible'] is False

def test_bad_generalization_does_not_rank_causal_factors():
    data=rows()
    for row in data[240:]:row['epm']=-row['epm']
    result=calculate_ml(data,settings(),COLS)['ml']
    assert result['status']=='not_better_than_baseline' and result['importance']==[]

def test_settings_reject_target_leakage_missing_columns_and_fake_time():
    for patch in [{'features':['epm']},{'unit_columns':['inline']},{'metrics':['epm','inline']},{'time_column':None}]:
        with pytest.raises(ValueError):Settings.model_validate(settings().model_dump()|patch)
    with pytest.raises(HTTPException):validate_settings(settings().model_copy(update={'features':['missing']}),COLS)
    with pytest.raises(HTTPException):validate_settings(settings().model_copy(update={'time_column':'recipe'}),COLS)

def test_real_source_fingerprint_tracks_late_updates_deletions_and_order(source):
    def fp(values):return data_fingerprint(f'WITH filtered AS (SELECT * FROM (VALUES {values}) v(id,value))',{})
    a=fp('(1,2),(2,3)')
    assert a==fp('(2,3),(1,2)')
    assert a['fingerprint']!=fp('(1,9),(2,3)')['fingerprint']
    assert a['fingerprint']!=fp('(1,2)')['fingerprint']
    assert a['fingerprint']!=fp('(1,2),(2,3),(2,3)')['fingerprint']

def test_real_sql_to_statistics_ml_summary_and_fingerprint(source):
    cte="""WITH filtered AS (SELECT i::text AS lot, (i%17)::float8 AS inline,
    ((i%17)*4+(i%2)*8)::float8 AS epm, CASE WHEN i%2=1 THEN 'A' ELSE 'B' END AS recipe,
    timestamp with time zone '2026-01-01' + i*interval '1 hour' AS measured FROM generate_series(1,300) i)"""
    result=compute(cte,{},settings(),COLS,stored(settings()))
    assert result['ml']['status']=='validated' and result['knowledge_eligible']
    assert result['data_fingerprint']==data_fingerprint(cte,{})['fingerprint']
    assert '검증 평균 절대 오차' in result['summary'] and '원인을 확정하지 않습니다' in result['summary']
