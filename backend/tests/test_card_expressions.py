import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import query
from app.card_expressions import compile_card_expression
from app.schemas import CardSpec, DashboardUpdate, QueryContext


def col(name):
    return {'kind': 'column', 'name': name}


def op(value):
    return {'kind': 'operator', 'value': value}


def num(value):
    return {'kind': 'number', 'value': str(value)}


YIELD = [col('passed_dies'), op('/'), col('tested_dies'), op('*'), num(100)]
COLUMNS = [{'name': name, 'type': dtype} for name, dtype in [
    ('passed_dies', 'real'), ('tested_dies', 'real'), ('group_name', 'text'),
    ('die_x', 'integer'), ('die_y', 'integer'), ('wafer_id', 'integer')]]


def card(**changes):
    return CardSpec.model_validate(dict(id='formula', title='Formula', dataset_id='isolated',
        dataset_version=1, kind='kpi', aggregate='sum', value_expression=YIELD) | changes)


@pytest.fixture
def run(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE cells (__row_id integer, wafer_id integer, die_x integer, die_y integer,
                            group_name text, passed_dies real, tested_dies real);
        INSERT INTO cells VALUES (1,1,2,3,'A',1,2), (2,1,2,3,'A',9,10), (3,1,4,5,'B',0,0);
    """)
    monkeypatch.setattr(query, 'validate_dataset', lambda spec: {})
    monkeypatch.setattr(query.database, 'workspace_token', lambda: 'isolated-formula-test')
    monkeypatch.setattr(query, '_dataset_cte', lambda *args: ('WITH filtered AS (SELECT * FROM cells)', {}, []))
    monkeypatch.setattr(query, 'execute_rows', lambda sql, params: [dict(row) for row in conn.execute(sql,
        {key: float(value) if isinstance(value, Decimal) else value for key, value in params.items()})])
    dataset = SimpleNamespace(columns=COLUMNS, spec={}, project_id=None)
    yield lambda **changes: query.chart_query(card(**changes), QueryContext(), dataset=dataset)
    conn.close()


def test_weighted_yield_matches_legacy_query_and_survives_save_serialization(run):
    legacy = run(value_expression=None, aggregate='yield')['rows'][0]['value']
    assert run()['rows'][0]['value'] == pytest.approx(legacy)
    assert legacy == pytest.approx(100 * 10 / 12)
    restored = CardSpec.model_validate_json(card(options={'value_format':'percent'}).model_dump_json())
    assert restored.value_expression == card().value_expression
    assert restored.options.value_format == 'percent'


@pytest.mark.parametrize('kind', ['kpi', 'bar', 'line', 'scatter', 'box', 'wafer'])
def test_expression_name_changes_only_label_and_survives_dashboard_serialization(run, kind):
    changes = dict(kind=kind)
    if kind in {'bar', 'line', 'scatter', 'box'}:
        changes['x'] = 'tested_dies'
    if kind in {'scatter', 'box'}:
        changes['aggregate'] = 'none'
    if kind in {'wafer'}:
        changes.update(x='die_x', y='die_y')
    named = card(**changes, value_expression_name='  수율  ')
    dashboard = DashboardUpdate(version=1, cards=[named], layout=[])
    restored = DashboardUpdate.model_validate_json(dashboard.model_dump_json()).cards[0]
    assert restored.value_expression_name == '수율'
    assert restored.value_expression == card().value_expression
    assert compile_card_expression(restored, COLUMNS) == compile_card_expression(card(**changes), COLUMNS)
    result = run(**changes, value_expression_name='수율')
    assert result['rows'] == run(**changes)['rows']
    assert result['meta']['value_label'] == '수율'
    assert run(**changes, value_expression_name=' ')['meta']['value_label'] == '직접 계산식'


def test_expression_names_are_optional_and_bounded():
    assert card().value_expression_name is None
    assert card(value_expression_name=' ').value_expression_name is None
    assert card(value_expression_name='a' * 80).value_expression_name == 'a' * 80
    with pytest.raises(ValidationError):
        card(value_expression_name='a' * 81)


@pytest.mark.parametrize('kind', ['bar', 'line', 'wafer'])
def test_grouped_and_cell_expressions_preserve_dimensions_and_zero_division_is_null(run, kind):
    changes = dict(kind=kind, x='group_name') if kind in {'bar','line'} else dict(kind=kind, x='die_x', y='die_y')
    rows = run(**changes)['rows']
    value = 'y' if kind in {'bar','line'} else 'value'
    assert rows[0][value] == pytest.approx(100 * 10 / 12)
    assert rows[1][value] is None
    if kind in {'wafer'}:
        assert (rows[0]['x'],rows[0]['y']) == (2,3)


@pytest.mark.parametrize('kind', ['bar', 'line', 'scatter', 'box'])
def test_raw_expressions_are_evaluated_per_row(run, kind):
    rows = run(kind=kind, x='tested_dies', aggregate='none')['rows']
    assert sorted(r['y'] for r in rows if r['y'] is not None) == [50,90]
    assert any(r['y'] is None for r in rows)


def test_precedence_parentheses_unary_sign_and_decimal_binding(run):
    tokens=[op('('),col('passed_dies'),op('+'),col('tested_dies'),op(')'),op('*'),op('-'),num('2.5')]
    assert run(value_expression=tokens)['rows'][0]['value'] == -55
    sql, params = compile_card_expression(card(value_expression=tokens), COLUMNS)
    assert '2.5' not in sql and params == {'card_expr_0':Decimal('2.5')}


@pytest.mark.parametrize('token', [op('%'), op('**'), op(';'), num('NaN'), num('1e999'), num('1;SELECT 1'),
    {'kind':'function','name':'SUM'}, {'kind':'column','name':'passed_dies','sql':'DROP TABLE cells'}])
def test_token_schema_blocks_unsupported_input(token):
    with pytest.raises(ValidationError):
        card(value_expression=[token])


@pytest.mark.parametrize('tokens', [[], [col('missing')], [col('group_name')], [num(1)],
    [col('passed_dies'),col('tested_dies')], [col('passed_dies'),op('+')],
    [op('('),col('passed_dies')], [op(')'),col('passed_dies')], [op('*'),col('passed_dies')]])
def test_invalid_or_incomplete_expressions_are_blocked_before_query(tokens):
    with pytest.raises(HTTPException) as error:
        compile_card_expression(card(value_expression=tokens), COLUMNS)
    assert error.value.status_code == 422


def test_excessive_expression_and_unsupported_aggregation_are_rejected():
    with pytest.raises(ValidationError):
        card(value_expression=[op('(')] * 101)
    for changes in [{'kind':'histogram'}, {'aggregate':'count'}, {'aggregate':'yield'}, {'aggregate':'none'}, {'kind':'scatter'}]:
        with pytest.raises(HTTPException):
            compile_card_expression(card(**changes), COLUMNS)


def test_registered_unusual_column_name_is_quoted_not_executable():
    name='value"; SELECT 9; --'
    sql, params=compile_card_expression(card(value_expression=[col(name)]), [{'name':name,'type':'numeric'}])
    assert sql == 'SUM(CAST("value""; SELECT 9; --" AS numeric))'
    assert params == {}
