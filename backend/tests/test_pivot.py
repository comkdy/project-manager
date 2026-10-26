import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

from app.db import engine
from app.dashboard_period import apply_period, period_columns
from app.direct_sql import parse_query
from app.pivot import pivot_definition, pivot_sql
from app.schemas import CardSpec, DatasetSpec, PivotSpec, QueryContext
from app.simple_editor import simple_editor_state


def source():
    return dict(id='measurements', schema_name='public', table_name='measurements', primary_key='id', project_column=None,
                columns=[{'name': name, 'type': dtype} for name, dtype in [
                    ('id','bigint'), ('lot_code','text'), ('wf_id','integer'), ('die_x','integer'), ('die_y','integer'),
                    ('operation','text'), ('value','numeric'), ('measured_at','timestamp without time zone')]])


def pivot(**changes):
    return PivotSpec(**dict(dict(keys=['lot_code','wf_id','die_x','die_y'],operation_column='operation',value_column='value',
                                time_column='measured_at',tie_breaker='id',outputs=[{'value':'A','name':'value_a'},{'value':'B','name':'value_b'}]),**changes))


def run(p, fixture=None, period=None):
    data=fixture or """('L1',1,1,2,'A',10.0,TIMESTAMP '2026-10-01 09:00',1),
('L1',1,1,2,'A',12.0,TIMESTAMP '2026-10-01 10:00',2),
('L1',1,1,2,'A',13.0,TIMESTAMP '2026-10-01 10:00',3),
('L1',1,1,2,'B',20.0,TIMESTAMP '2026-10-01 11:00',4),
('L1',1,1,2,'B',999.0,NULL,5),
('L1',1,2,2,'A',30.0,TIMESTAMP '2026-10-01 09:00',6),
('L1',1,3,2,'A',40.0,TIMESTAMP '2026-10-01 09:00',7),
('L1',1,3,2,'B',50.0,TIMESTAMP '2026-10-01 09:00',8),
('L1',1,3,2,'A',NULL,TIMESTAMP '2026-10-01 10:00',9),
(NULL,1,4,2,'A',100.0,TIMESTAMP '2026-10-01 09:00',10),
(NULL,1,4,2,'B',200.0,TIMESTAMP '2026-10-01 09:00',11)"""
    sql=pivot_sql(p,source())
    if period:
        compiled=dict(source=source(),sources=[source()],direct_sql=sql,spec=DatasetSpec(source_id='measurements',pivot=p))
        card=CardSpec(id='pivot',title='pivot',dataset_id='measurements',dataset_version=1,kind='scatter',x='value_a',y='value_b')
        filtered,meta=apply_period(compiled,card,QueryContext(period_start=period))
        assert compiled['direct_sql']==sql and meta['column']=='measured_at'
        sql=filtered['direct_sql']
    sql=sql.replace('public.measurements','measurements')
    sql='WITH measurements(lot_code,wf_id,die_x,die_y,operation,value,measured_at,id) AS (VALUES '+data+'), '+sql.removeprefix('WITH ')
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        assert connection.exec_driver_sql('SHOW transaction_read_only').scalar()=='on'
        return [dict(row) for row in connection.execute(text(sql)).mappings()]


def test_latest_values_are_independent_and_ties_missing_values_and_keys_are_explicit():
    rows=run(pivot())
    assert rows==[dict(lot_code='L1',wf_id=1,die_x=1,die_y=2,value_a=13,value_b=20)]
    rows=run(pivot(require_all=False))
    assert len(rows)==3
    assert rows[1]['value_a']==30 and rows[1]['value_b'] is None
    assert rows[2]['value_a'] is None and rows[2]['value_b']==50


def test_input_filters_apply_before_last_selection():
    rows=run(pivot(filters=[{'column':'measured_at','op':'lt','value':'2026-10-01 10:00'}],require_all=False))
    assert rows[0]['value_a']==10 and rows[0]['value_b'] is None


def test_dashboard_period_uses_pivot_measurement_time_before_last_selection():
    fixture="""('L1',1,1,2,'A',10.0,TIMESTAMP '2026-09-20 09:00',1),
    ('L1',1,1,2,'B',20.0,TIMESTAMP '2026-10-01 09:00',2)"""
    assert len(run(pivot(),fixture))==1
    assert run(pivot(),fixture,period={'year':2026,'week':40})==[]
    rows=run(pivot(require_all=False),fixture,period={'year':2026,'week':40})
    assert rows[0]['value_a'] is None and rows[0]['value_b']==20
    ambiguous=source()
    ambiguous['columns'].append({'name':'imported_at','type':'timestamp'})
    compiled=dict(source=ambiguous,sources=[ambiguous],spec=DatasetSpec(source_id='measurements',pivot=pivot()))
    assert period_columns(compiled)['default']['column']=='measured_at'


def test_internal_alias_does_not_shadow_a_source_key():
    unusual=source()
    unusual['columns'][1]['name']='__pivot_value'
    sql=pivot_sql(pivot(keys=['__pivot_value','wf_id','die_x','die_y']),unusual)
    assert 'AS __pivot_value_' in sql
    assert 'MAX(__pivot_value_)' in sql


def test_recipe_roundtrip_is_lossless_and_arbitrary_sql_cannot_masquerade_as_pivot():
    form=DatasetSpec(source_id='measurements',pivot=pivot())
    saved=pivot_definition(form,source())
    state=simple_editor_state(saved.raw_sql,source(),saved.pivot)
    assert state['available']
    assert pivot_definition(DatasetSpec(**state['spec']),source()).raw_sql==saved.raw_sql
    assert not simple_editor_state(saved.raw_sql+' LIMIT 1',source(),saved.pivot)['available']
    with pytest.raises(HTTPException):
        pivot_definition(saved.model_copy(update={'raw_sql':saved.raw_sql+' LIMIT 1'}),source())
    assert not simple_editor_state(saved.raw_sql,source())['available']


def test_literals_and_identifiers_cannot_inject_sql():
    dangerous="A'); DROP TABLE measurements; --"
    p=pivot(outputs=[{'value':dangerous,'name':'측정 "값"'}],require_all=False)
    sql=pivot_sql(p,source());parse_query(sql)
    rows=run(p)
    assert rows==[]
    with pytest.raises(HTTPException):
        pivot_sql(pivot(time_column='missing'),source())
    with pytest.raises(HTTPException):
        pivot_sql(pivot(value_column='lot_code',keys=['wf_id','die_x','die_y']),source())


@pytest.mark.parametrize('changes',[
    {'keys':[]}, {'keys':['lot_code','lot_code']}, {'keys':['operation']},
    {'tie_breaker':'measured_at'}, {'outputs':[]},
    {'outputs':[{'value':'A','name':'lot_code'}]},
    {'outputs':[{'value':'A','name':'x'},{'value':'A','name':'y'}]},
    {'outputs':[{'value':'A','name':'한'*30}]},
])
def test_invalid_recipes_are_rejected(changes):
    with pytest.raises((ValidationError,HTTPException)):
        pivot(**changes)
