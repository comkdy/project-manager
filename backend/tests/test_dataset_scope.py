from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import project_settings
from app.dataset_scope import scoped_sql, source_predicate, dataset_kind, project_dataset_id
from app.direct_sql import parse_query
from app.table_visibility import TableVisibility
from app.schemas import DatasetSpec
from app.source_discovery import default_sql
from app.query import source_json
from test_project_datasets import api


def test_every_physical_source_is_scoped_inside_cte_union_and_subqueries(monkeypatch):
    from sqlglot import exp
    monkeypatch.setattr(project_settings,'managed_project',lambda _:SimpleNamespace(lot_cds=['XXXA']))
    source=dict(id='s',schema_name='public',table_name='facts',columns=[{'name':'lot_cd'}],project_column=None)
    sql='WITH f AS (SELECT value FROM facts) SELECT AVG(value) FROM f UNION ALL SELECT AVG(value) FROM public.facts WHERE lot_cd = \'XXXB\' OR TRUE'
    output=scoped_sql({'direct_sql':sql,'sources':[source]},5)
    assert output.count('"lot_cd" IN (\'XXXA\')')==2
    assert len(list(parse_query(output).find_all(exp.Table)))==3  # two physical references and one CTE name
    assert sql.count('XXXA')==0


def test_unmapped_source_blocks_project_read_and_literals_are_safe(monkeypatch):
    monkeypatch.setattr(project_settings,'managed_project',lambda _:SimpleNamespace(lot_cds=["X' OR TRUE --"]))
    source=dict(id='s',schema_name='public',table_name='facts',columns=[{'name':'lot_cd'}],project_column=None)
    value=source_predicate(source,5,SimpleNamespace(lot_cds=["X' OR TRUE --"]))
    assert value.expressions[0].this=="X' OR TRUE --"
    source['columns']=[]
    with pytest.raises(HTTPException) as error:
        scoped_sql({'direct_sql':'SELECT * FROM facts','sources':[source]},5)
    assert error.value.status_code==422
    assert scoped_sql({'direct_sql':'SELECT * FROM facts','sources':[source]},None)=='SELECT * FROM facts'


def test_origin_is_verified_from_identity_and_direct_table_definition(monkeypatch):
    monkeypatch.setattr(project_settings,'managed_project',lambda _:SimpleNamespace(lot_cds=['XXXA']))
    source=SimpleNamespace(id='source',name='facts',schema_name='public',table_name='facts',primary_key='id',
        project_column=None,columns=[{'name':'id','type':'integer'},{'name':'lot_cd','type':'text'}])
    catalog=TableVisibility([source])
    sql=default_sql(source_json(source),5,lot_cds=['XXXA'])
    dataset=SimpleNamespace(id=project_dataset_id(5,'source'),project_id=5,spec=DatasetSpec(source_id='source',raw_sql=sql).model_dump())
    assert dataset_kind(dataset,catalog)=='source'
    dataset.spec['raw_sql']=sql+' AND id > 100'
    assert dataset_kind(dataset,catalog)=='derived'
    dataset.id='user-created';dataset.spec['raw_sql']=sql
    assert dataset_kind(dataset,catalog)=='derived'


@pytest.mark.parametrize('sql',[
 'SELECT DISTINCT project_id FROM inline_measurements',
 'WITH m AS (SELECT * FROM inline_measurements) SELECT project_id, COUNT(*) AS n FROM m GROUP BY project_id',
 'SELECT project_id FROM inline_measurements WHERE project_id=2 OR TRUE GROUP BY project_id',
 'SELECT project_id FROM inline_measurements UNION SELECT project_id FROM inline_measurements WHERE project_id=2',
 'SELECT a.project_id, b.project_id AS other FROM inline_measurements a JOIN wafers b ON a.wafer_id=b.id GROUP BY a.project_id,b.project_id',
 'SELECT public.inline_measurements.project_id FROM public.inline_measurements GROUP BY public.inline_measurements.project_id',
])
def test_project_predicate_precedes_projection_join_group_and_union(api,sql):
    response=api.post('/api/datasets/preview',json={'spec':{'source_id':'inline_measurements','raw_sql':sql},'context':{'project_id':1}})
    assert response.status_code==200,response.text
    assert response.json()['rows'] and {r['project_id'] for r in response.json()['rows']}=={1}
    assert all(r.get('other',1)==1 for r in response.json()['rows'])


def test_dropped_scope_column_cannot_hide_cross_project_aggregation(api):
    sql='SELECT COUNT(*) AS n FROM inline_measurements'
    body={'spec':{'source_id':'inline_measurements','raw_sql':sql},'context':{'project_id':1}}
    scoped=api.post('/api/datasets/preview',json=body)
    expected=api.post('/api/datasets/preview',json={**body,'spec':{**body['spec'],'raw_sql':sql+' WHERE project_id=1'},'context':{}})
    common=api.post('/api/datasets/preview',json={**body,'context':{}})
    assert scoped.status_code==expected.status_code==common.status_code==200
    assert scoped.json()['rows']==expected.json()['rows']
    assert scoped.json()['rows'][0]['n']<common.json()['rows'][0]['n']
