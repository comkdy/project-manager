"""Period filtering preserves form semantics without introducing a window sort."""
from copy import deepcopy

import pytest

from app.dashboard_period import apply_period, filter_source
from app.query import _dataset_cte, execute_rows
from app.raw_sql import compile_form, form_to_sql
from app.schemas import CardSpec, DatasetSpec, Filter, QueryContext


SOURCE = dict(id='events', schema_name='public', table_name='events', primary_key='id', project_column='project_id',
              columns=[{'name':name,'type':dtype} for name,dtype in [('id','integer'),('project_id','integer'),
                       ('occurred_at','timestamp with time zone'),('value','integer')]])
ROWS = "(VALUES (1,1,TIMESTAMPTZ '2026-09-27 14:59:59+00',100), (2,1,TIMESTAMPTZ '2026-09-27 15:00:00+00',2), (3,1,TIMESTAMPTZ '2026-10-02 00:00+09',3), (4,2,TIMESTAMPTZ '2026-10-02 00:00+09',50), (5,1,NULL,200)) AS fixture(id,project_id,occurred_at,value)"


@pytest.mark.parametrize('grouped',[False,True])
def test_form_period_matches_previous_sql_path_before_aggregation(grouped):
    spec=DatasetSpec(source_id='events', columns=([{'name':'project_id','expression':'project_id'},
         {'name':'total','expression':'SUM(value)'}] if grouped else []),group_by=['project_id'] if grouped else [])
    compiled={**compile_form(spec,SOURCE), 'spec':spec,'source':deepcopy(SOURCE),'relations':[],
              'base_columns':SOURCE['columns']}
    before=deepcopy(compiled)
    card=CardSpec(id='period',title='Period',dataset_id='events',dataset_version=1,kind='kpi',aggregate='count')
    context=QueryContext(project_id=1,period_start={'year':2026,'week':40})
    updated,metadata=apply_period(compiled,card,context)
    filters=[] if grouped else [Filter(column='value',op='gte',value=3)]
    cte,params,_=_dataset_cte(updated,context,filters)
    assert 'ROW_NUMBER' not in cte
    assert compiled==before
    column={'column':'occurred_at','type':'timestamp with time zone'}
    old_sql=filter_source(form_to_sql(spec,SOURCE,context.project_id),SOURCE,column,context.period_start)
    old_cte,old_params,_=_dataset_cte({**compiled,'direct_sql':old_sql},context,filters)
    projection='project_id,total' if grouped else 'id,project_id,value'
    order='project_id' if grouped else 'id'
    def run(sql,bindings):
        sql=sql.replace('"public"."events"',ROWS).replace('public.events',ROWS)
        return execute_rows(f'{sql} SELECT {projection} FROM filtered ORDER BY {order}',bindings)
    actual=run(cte,params)
    assert actual==run(old_cte,old_params)
    assert actual==([{'project_id':1,'total':5}] if grouped else [{'id':3,'project_id':1,'value':3}])
    assert metadata['timezone']=='Asia/Seoul'


def test_period_on_joined_form_source_preserves_left_join_rows():
    target={**deepcopy(SOURCE),'id':'targets','table_name':'targets'}
    relation={'id':'link','left_column':'id','right_column':'id','join_type':'left'}
    relations=[(relation,target)]
    spec=DatasetSpec(source_id='events',joins=['link'],where_sql='id IN (2, 3)',columns=[
        {'name':'id','expression':'id'},{'name':'reading','expression':'targets__value'}])
    compiled={**compile_form(spec,SOURCE,relations),'spec':spec,'source':SOURCE,'relations':relations,
              'base_columns':SOURCE['columns']+[
                  {**c,'name':'targets__'+c['name']} for c in target['columns']]}
    card=CardSpec(id='join',title='Join',dataset_id='events',dataset_version=1,kind='kpi',aggregate='count',
                  period={'source_id':'targets','column':'occurred_at'})
    context=QueryContext(project_id=1,period_start={'year':2026,'week':40})
    updated,_=apply_period(compiled,card,context)
    cte,params,_=_dataset_cte(updated,context)
    target_rows="(VALUES (2,1,TIMESTAMPTZ '2026-01-01',7),(3,1,TIMESTAMPTZ '2026-10-01',9)) AS target_fixture(id,project_id,occurred_at,value)"
    sql=cte.replace('"public"."events"',ROWS).replace('"public"."targets"',target_rows)
    assert execute_rows(sql+' SELECT id,reading FROM filtered ORDER BY id',params)==[
        {'id':2,'reading':None},{'id':3,'reading':9}]
