import pytest

from app.editor_filters import editable_filters
from app.raw_sql import compile_raw_sql, form_to_sql
from app.schemas import DatasetSpec
from app.simple_editor import prepare_simple_spec, simple_editor_state
from test_raw_sql import SOURCE


def editable(sql):
    return editable_filters(compile_raw_sql(sql, SOURCE)['spec'])


def test_loaded_conditions_remain_editable_across_preview_and_sql_roundtrips():
    form = editable("SELECT *, value * 1000 AS scaled FROM inline_measurements WHERE metric = 'thickness' AND value * 1000 > 95000")
    assert form.where_sql is None
    assert [(f.column, f.op, f.value) for f in form.filters] == [('metric', 'eq', 'thickness'), ('scaled', 'gt', 95000)]
    again = editable(form_to_sql(form, SOURCE))
    assert again.filters == form.filters
    # Removing a visible filter must remove the actual SQL predicate, too.
    again.filters.pop()
    sql = form_to_sql(again, SOURCE)
    assert '95000' not in sql
    assert 'thickness' in sql


@pytest.mark.parametrize('op', ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains'])
def test_every_builder_operator_survives_conversion(op):
    value = "O'Brien_100%\\" if op == 'contains' else -4.25
    original = DatasetSpec(source_id='inline', filters=[{'column':'metric' if op == 'contains' else 'value', 'op':op, 'value':value}])
    form = editable(form_to_sql(original, SOURCE))
    assert form.filters == original.filters
    assert form.where_sql is None


def test_complex_and_unprojected_conditions_are_preserved_without_splitting_or():
    form = editable("SELECT value FROM inline_measurements WHERE value > 5 AND (metric = 'A' OR value < 10) AND id IN (1, 2) AND value IS NOT NULL")
    assert len(form.filters) == 1
    assert form.filters[0].value == 5
    assert ' OR ' in form.where_sql
    assert ' IN ' in form.where_sql
    assert 'IS NULL' in form.where_sql
    assert ' > 5' not in form.where_sql
    again = editable(form_to_sql(form, SOURCE))
    assert again.filters == form.filters
    assert again.where_sql == form.where_sql


def test_grouped_where_stays_before_aggregation():
    form = editable("SELECT metric, COUNT(*) AS n FROM inline_measurements WHERE metric = 'A' GROUP BY metric HAVING COUNT(*) > 1")
    assert form.filters == []
    assert form.where_sql == '"metric" = \'A\''
    sql = form_to_sql(form, SOURCE)
    assert sql.index('WHERE') < sql.index('GROUP BY') < sql.index('HAVING')


def test_large_numbers_and_excess_conditions_are_not_lost():
    form = editable('SELECT id FROM inline_measurements WHERE id > 9007199254740993')
    assert form.filters[0].value == '9007199254740993'
    form = editable('SELECT value FROM inline_measurements WHERE ' + ' AND '.join(f'value <> {n}' for n in range(31)))
    assert len(form.filters) == 30
    assert '30' in form.where_sql


def test_arbitrary_like_wildcards_stay_in_sql():
    form = editable("SELECT metric FROM inline_measurements WHERE CAST(metric AS TEXT) ILIKE '%a_b%'")
    assert form.filters == []
    assert "'%a_b%'" in form.where_sql


@pytest.mark.parametrize('column,values', [
    ('id', [1, 3, -5]),
    ('metric', ['thickness', 'width', "O'Brien_100%", 'A,B', "x'); DROP TABLE wafers;--"]),
])
@pytest.mark.parametrize('op', ['in', 'not_in'])
def test_in_filter_roundtrip_stays_visible_and_removable(column, values, op):
    original = DatasetSpec(source_id='inline', filters=[{'column': column, 'op': op, 'value': values}])
    sql = form_to_sql(prepare_simple_spec(original, SOURCE), SOURCE)
    assert ' IN (' in sql and 'ILIKE' not in sql
    if op == 'not_in':
        assert ' NOT IN (' in sql
    state = simple_editor_state(sql, SOURCE)
    assert state['available']
    form = DatasetSpec.model_validate(state['spec'])
    assert form.filters == original.filters
    assert form.where_sql is None
    form.filters.clear()
    assert 'WHERE' not in form_to_sql(prepare_simple_spec(form, SOURCE), SOURCE)


@pytest.mark.parametrize('op,operator', [('in', 'IN'), ('not_in', 'NOT IN')])
def test_in_on_unprojected_grouped_input_remains_where(op, operator):
    state = simple_editor_state(f"SELECT metric, AVG(value) AS mean FROM inline_measurements WHERE id {operator} (1, 3) GROUP BY metric", SOURCE)
    assert state['available']
    spec = DatasetSpec.model_validate(state['spec'])
    assert spec.filters[0].model_dump() == {'column': 'id', 'op': op, 'value': [1, 3]}
    sql = form_to_sql(prepare_simple_spec(spec, SOURCE), SOURCE)
    assert 'HAVING' not in sql
    assert sql.index('WHERE') < sql.index('GROUP BY')


@pytest.mark.parametrize('values', ['1, NULL', '1, value', ', '.join(str(i) for i in range(101))])
@pytest.mark.parametrize('operator', ['IN', 'NOT IN'])
def test_in_with_unsupported_values_is_preserved_in_sql(values, operator):
    sql = f'SELECT id FROM inline_measurements WHERE id {operator} ({values})'
    form = editable(sql)
    assert form.filters == []
    assert ' IN ' in form.where_sql


def test_parenthesized_not_in_is_editable_but_not_a_compound_negation():
    form = editable('SELECT id FROM inline_measurements WHERE NOT (id IN (1, 3))')
    assert form.where_sql is None
    assert form.filters[0].model_dump() == {'column': 'id', 'op': 'not_in', 'value': [1, 3]}
    form = editable('SELECT id FROM inline_measurements WHERE NOT (id IN (1, 3) AND value > 0)')
    assert not form.filters and 'NOT' in form.where_sql


def test_empty_not_in_excludes_nothing():
    spec = DatasetSpec(source_id='inline', filters=[{'column': 'id', 'op': 'not_in', 'value': []}])
    sql = form_to_sql(prepare_simple_spec(spec, SOURCE), SOURCE)
    assert 'TRUE' in sql and 'IN ()' not in sql
