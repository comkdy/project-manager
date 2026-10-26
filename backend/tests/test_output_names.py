import pytest
from fastapi import HTTPException

from app.expressions import quote, quote_output
from app.raw_sql import compile_raw_sql, form_to_sql
from app.schemas import DatasetSpec
from test_raw_sql import SOURCE
from test_project_datasets import api


@pytest.mark.parametrize('name', ['value * 1.5', 'AVG(value)', '"value" + 1', '평균 값'])
def test_expression_labels_survive_sql_roundtrip(name):
    spec = DatasetSpec(source_id='inline', columns=[{'name': name, 'expression': 'value * 1.5'}])
    form = compile_raw_sql(form_to_sql(spec, SOURCE), SOURCE)['spec']
    assert form.columns[0].name == name
    assert form_to_sql(form, SOURCE) == form_to_sql(spec, SOURCE)


def test_output_quoting_does_not_widen_physical_identifier_rules():
    assert quote_output('value * 1.5') == '"value * 1.5"'
    assert quote_output('x"; SELECT 1; --') == '"x""; SELECT 1; --"'
    with pytest.raises(HTTPException):
        quote('value * 1.5')
    for name in ['', 'x\x00y', 'line\nname', 'x' * 64, '이름' * 11]:
        with pytest.raises(HTTPException):
            quote_output(name)


@pytest.mark.parametrize('name,expression', [
    ('value * 1.5', 'value * 1.5'),
    ('"value" + 1', 'value + 1'),
    ("':value'", "':value'"),
    ('x"; SELECT 1; --', 'value'),
])
def test_expression_label_preview_sort_filter_and_sql_view(api, name, expression):
    spec = {'source_id':'inline_measurements', 'columns':[
        {'name':'id', 'expression':'id'}, {'name':name, 'expression':expression}],
        'filters':[{'column':name, 'op':'contains', 'value':':'} if name == "':value'" else {'column':name, 'op':'ne', 'value':None}]}
    context = {'project_id':1, 'limit':3, 'sort':name, 'direction':'desc'}
    response = api.post('/api/datasets/preview',json={'spec':spec,'context':context})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['rows']) == 3
    assert all(name in row and row[name] is not None for row in result['rows'])
    assert result['columns'][1]['name'] == name
    # SQL execution takes its ordering from the visible query.
    label = '"' + name.replace('"', '""') + '"'
    sql = result['canonical_sql'] + f'\nORDER BY {label} DESC NULLS LAST, id DESC'
    roundtrip = api.post('/api/datasets/preview',json={
        'spec':{'source_id':'inline_measurements','raw_sql':sql}, 'context':context})
    assert roundtrip.status_code == 200, roundtrip.text
    assert roundtrip.json()['rows'] == result['rows']
    assert roundtrip.json()['canonical_sql'] == sql
