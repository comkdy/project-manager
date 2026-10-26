"""Inline identity follows source predicates, not ambiguous output names."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.measurement_items import item_context, validate_items
from app.source_compatibility import columns_preserved, dataset_compatible


def dataset(sql, names=('value',)):
    return {'spec': {'raw_sql': sql}, 'columns': [{'name': n} for n in names]}


def test_same_parameter_in_two_operations_is_two_items():
    d = dataset("""SELECT a.value av,b.value bv FROM inline_data a
        JOIN inline_data b ON a.lot_id=b.lot_id AND a.wf_id=b.wf_id
        WHERE a.operation='oper A CD' AND a.parameter='MAIN'
        AND b.operation='oper B CD' AND b.parameter='MAIN'""", ('av','bv'))
    assert item_context(d)['labels'] == {'av':'Inline oper A CD / MAIN','bv':'Inline oper B CD / MAIN'}


@pytest.mark.parametrize('where', ["parameter='MAIN'", "operation='oper A CD'",
    "(operation='oper A CD' OR operation='oper B CD') AND parameter='MAIN'", 'true'])
def test_unknown_or_multiple_items_cannot_be_analyzed_as_one(where):
    d = dataset('SELECT value FROM inline_data WHERE ' + where)
    with pytest.raises(HTTPException, match='operation\\+parameter'):
        validate_items(d, SimpleNamespace(metrics=['value'], features=[]))


def test_cte_and_card_filters_resolve_underlying_pair():
    d = dataset('WITH x AS (SELECT operation,parameter,value FROM inline_data) SELECT * FROM x')
    context = item_context(d, [{'column':'operation','op':'eq','value':'oper C CD'},
                               {'column':'parameter','op':'eq','value':'MAIN'}])
    assert context['labels']['value'] == 'Inline oper C CD / MAIN'


def test_recipe_and_equipment_are_operation_specific():
    d = dataset("SELECT recipe,eqp_id FROM wafer_his WHERE operation='oper B'", ('recipe','eqp_id'))
    assert item_context(d)['labels'] == {'recipe':'공정 oper B / Recipe','eqp_id':'공정 oper B / 장비 eqp_id'}


def test_joined_other_source_operation_cannot_label_measurement():
    d = dataset("""WITH x AS (SELECT a.value, b.operation, b.parameter
        FROM inline_data a JOIN inline_data b ON a.lot_id=b.lot_id)
        SELECT value FROM x WHERE operation='oper B CD' AND parameter='MAIN'""")
    assert item_context(d)['unresolved_columns'] == ['value']
    assert item_context(d)['labels'] == {}


def test_alias_does_not_invent_source_meaning():
    d = dataset('SELECT measurement AS inline_main FROM custom_measurements', ('inline_main',))
    assert item_context(d)['labels'] == {}


@pytest.mark.parametrize('old,new,expected', [
    ({'a':'text'}, {'a':'text','eqp_id':'text'}, True),
    ({'a':'text'}, {'a':'integer'}, False), ({'a':'text'}, {}, False),
    ([{'name':'a','type':'text'}], [{'name':'a','type':'text'},{'name':'eqp_id','type':'text'}], True),
    ([{'name':'a','type':'text'}], [{'name':'a','type':'integer'}], False),
])
def test_column_compatibility_allows_only_additions(old,new,expected):
    assert columns_preserved(old,new) is expected


def test_dataset_changes_still_invalidate_history():
    old = {'id':'d','version':1,'sources':[{'id':'s','table_name':'wafer_his','visible':True,'columns':{'recipe':'text'}}]}
    new = {**old,'sources':[{**old['sources'][0],'columns':{'recipe':'text','eqp_id':'text'}}]}
    assert dataset_compatible(old,new)
    assert not dataset_compatible(old,{**new,'version':2})
    for field,value in [('visible',False), ('table_name','different')]:
        assert not dataset_compatible(old,{**new,'sources':[{**new['sources'][0],field:value}]})
