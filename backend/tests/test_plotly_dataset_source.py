"""Python cards receive original dataset columns and own their transformations."""
import ast
import pytest
from fastapi import HTTPException

from app import plotly_scripts, query
from app.plotly_codegen import dataset_script, expanded_dataset_script, simple_script
from app.plotly_scripts import run_script
from app.schemas import CardSpec, QueryContext


def spec(kind='bar', **updates):
    return {'kind': kind, 'title': 'Dataset chart', 'x': 'batch', 'y': 'reading',
            'aggregate': 'none', 'color': None, 'trellis': None, 'options': {}, **updates}


def schema(rows, extra=()):
    names = list(dict.fromkeys([name for row in rows for name in row] + list(extra)))
    return [{'name': name, 'type': 'text' if any(isinstance(r.get(name), str) for r in rows)
             else 'double precision'} for name in names]


def execute(card, rows, *, columns=None, numeric_color=False, suffix=''):
    columns = columns or schema(rows)
    source = dataset_script(card, columns, numeric_color=numeric_color)
    tree = ast.parse(source)
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == 'copy' for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Name) and node.id == 'data' for node in ast.walk(tree))
    return run_script(source + suffix, rows, columns), source


def points(figure):
    return [(x, y) for trace in figure['data'] for x, y in zip(trace.get('x', []), trace.get('y', []))]


@pytest.mark.parametrize('kind', ['bar', 'line', 'scatter', 'box'])
def test_unaggregated_kinds_read_dataset_names_and_show_current_rows(kind):
    rows = [{'batch': 'A', 'reading': 7}, {'batch': 'B', 'reading': 3}]
    result, source = execute(spec(kind, options={'sort': 'desc'}), rows)
    assert points(result) == ([('B', 3), ('A', 7)] if kind in {'bar', 'line'}
                              else [('A', 7), ('B', 3)])
    assert 'batch' in source and 'reading' in source
    refreshed = run_script(source, [{'batch': 'C', 'reading': 13}], schema(rows))
    assert points(refreshed) == [('C', 13)]


@pytest.mark.parametrize(('aggregate', 'expected'), [
    ('count', 3), ('sum', 10), ('avg', 5), ('min', 2), ('max', 8),
])
def test_grouped_aggregation_matches_sql_null_semantics(aggregate, expected):
    rows = [{'batch': 'A', 'reading': 2}, {'batch': 'A', 'reading': 8},
            {'batch': 'A', 'reading': None}, {'batch': 'B', 'reading': None},
            {'batch': None, 'reading': 4}]
    figure, source = execute(spec(aggregate=aggregate), rows)
    result = dict(points(figure))
    assert result['A'] == expected
    assert result['B'] == (1 if aggregate == 'count' else None)
    assert result[None] == (1 if aggregate == 'count' else 4)
    assert 'groupby' in source


@pytest.mark.parametrize(('aggregate', 'expected'), [
    ('none', 3), ('count', 3), ('sum', 10), ('avg', 5), ('min', 2), ('max', 8),
])
def test_kpi_aggregations_execute_in_displayed_source(aggregate, expected):
    rows = [{'batch': 'A', 'reading': 2}, {'batch': 'B', 'reading': 8},
            {'batch': 'B', 'reading': None}]
    figure, _ = execute(spec('kpi', aggregate=aggregate), rows)
    assert figure['data'][0]['value'] == expected


def test_weighted_yield_uses_summed_components_and_handles_zero_denominator():
    rows = [{'batch': 'A', 'passed_dies': 1, 'tested_dies': 2},
            {'batch': 'A', 'passed_dies': 9, 'tested_dies': 10},
            {'batch': 'B', 'passed_dies': 0, 'tested_dies': 0}]
    figure, source = execute(spec(aggregate='yield', y=None), rows)
    actual = dict(points(figure))
    assert actual['A'] == pytest.approx(100 * 10 / 12)
    assert actual['B'] is None
    assert 'passed_dies' in source and 'tested_dies' in source
    kpi, _ = execute(spec('kpi', aggregate='yield', y=None), rows)
    assert kpi['data'][0]['value'] == pytest.approx(100 * 10 / 12)


EXPRESSION = [{'kind': 'column', 'name': 'passed_dies'}, {'kind': 'operator', 'value': '/'},
              {'kind': 'column', 'name': 'tested_dies'}, {'kind': 'operator', 'value': '*'},
              {'kind': 'number', 'value': '100'}]


def test_calculated_value_aggregates_before_dividing_and_is_editable():
    rows = [{'batch': 'A', 'passed_dies': 1, 'tested_dies': 2},
            {'batch': 'A', 'passed_dies': 9, 'tested_dies': 10},
            {'batch': 'B', 'passed_dies': 0, 'tested_dies': 0}]
    card = spec(aggregate='sum', y=None, value_expression=EXPRESSION, value_expression_name='수율')
    figure, source = execute(card, rows)
    assert dict(points(figure))['A'] == pytest.approx(100 * 10 / 12)
    assert dict(points(figure))['B'] is None
    # This change is made directly in the displayed Python, with no CardSpec edit.
    updated = source.replace('100.0', '10.0') if '100.0' in source else source.replace('100', '10')
    changed = run_script(updated, rows, schema(rows))
    assert dict(points(changed))['A'] == pytest.approx(10 * 10 / 12)
    raw, _ = execute({**card, 'kind': 'scatter', 'aggregate': 'none'}, rows)
    assert [y for _, y in points(raw)] == [50, 90, None]


def test_calculation_keeps_parentheses_decimal_and_unary_precedence():
    tokens = [{'kind': 'operator', 'value': '('}, {'kind': 'column', 'name': 'passed_dies'},
              {'kind': 'operator', 'value': '+'}, {'kind': 'column', 'name': 'tested_dies'},
              {'kind': 'operator', 'value': ')'}, {'kind': 'operator', 'value': '*'},
              {'kind': 'operator', 'value': '-'}, {'kind': 'number', 'value': '2.5'}]
    rows = [{'passed_dies': 1, 'tested_dies': 2}, {'passed_dies': 9, 'tested_dies': 10}]
    figure, _ = execute(spec('kpi', aggregate='sum', value_expression=tokens), rows)
    assert figure['data'][0]['value'] == -55


def test_histogram_owns_bins_ignores_null_and_uses_global_bounds_for_panels():
    rows = [{'reading': n, 'wafer': 'W1' if n in (0, 4, 8) else 'W2'}
            for n in [0, 2, 4, 6, 8, 10, None]]
    figure, source = execute(spec('histogram', x='reading', y=None,
                                  trellis='wafer', options={'bins': 5}), rows)
    counts = {}
    for x, y in points(figure):
        counts[x] = counts.get(x, 0) + y
    assert counts == {1: 1, 3: 1, 5: 1, 7: 1, 9: 2}
    assert sum(counts.values()) == 6
    constant, _ = execute(spec('histogram', x='reading', y=None, options={'bins': 5}),
                          [{'reading': 4}, {'reading': 4}, {'reading': None}])
    assert points(constant) == [(4, 2)]
    assert 'bin_count = 5' in source
    changed = run_script(source.replace('bin_count = 5', 'bin_count = 10'), rows, schema(rows))
    assert sorted(x for x, _ in points(changed)) == [0.5, 2.5, 4.5, 6.5, 8.5, 9.5]


def test_trellis_null_panel_and_duplicate_dimension_names_keep_groups():
    rows = [{'batch': 'A', 'reading': 2}, {'batch': 'A', 'reading': 4},
            {'batch': None, 'reading': 8}]
    figure, _ = execute(spec(aggregate='sum', color='batch', trellis='batch'), rows)
    assert sorted(y for _, y in points(figure)) == [6, 8]
    assert len(figure['data']) == 2
    assert [a['text'] for a in figure['layout']['annotations']] == ['A', '(빈 값)']


def test_original_df_names_and_values_survive_internal_derived_column_collisions():
    rows = [{'batch': 'A', 'reading': 2, 'x': 101, 'y': 201, 'color': 'first',
             'trellis': 'original', 'value': 900},
            {'batch': 'A', 'reading': 4, 'x': 102, 'y': 202, 'color': 'second',
             'trellis': 'original', 'value': 901}]
    suffix = '\nfig = go.Figure(go.Bar(x=df["x"].tolist(), y=df["y"].tolist(), customdata=df["color"].tolist()))'
    figure, _ = execute(spec('scatter'), rows, suffix=suffix)
    assert points(figure) == [(101, 201), (102, 202)]
    assert figure['data'][0]['customdata'] == ['first', 'second']
    aliases, _ = execute(spec(x='x', y='y', color='color', trellis='trellis', aggregate='sum'), rows)
    assert sorted(points(aliases)) == [(101, 201), (102, 202)]
    collision_rows = [{'reading': 2, 'sum_reading': 44, 'row_count': 11},
                      {'reading': 4, 'sum_reading': 44, 'row_count': 12}]
    grouped, source = execute(spec(x='sum_reading', aggregate='sum'), collision_rows)
    assert points(grouped) == [(44, 6)]
    assert "df['sum_reading_2']" in source


def test_unusual_source_names_stay_literal_column_identifiers():
    name = '측정 "값"\nfig = go.Figure(go.Pie(values=[999]))\n#'
    rows = [{'lot code': 'A', name: 2}, {'lot code': 'A', name: 3}]
    figure, _ = execute(spec(x='lot code', y=name, aggregate='sum'), rows)
    assert figure['data'][0]['type'] == 'bar'
    assert points(figure) == [('A', 5)]


def test_wafer_averages_color_per_cell_with_original_names():
    rows = [{'die_x': 1, 'die_y': 2, 'resistance': 2, 'wafer_id': 1},
            {'die_x': 1, 'die_y': 2, 'resistance': 8, 'wafer_id': 1},
            {'die_x': 2, 'die_y': 2, 'resistance': 4, 'wafer_id': 1}]
    figure, source = execute(spec('wafer', x='die_x', y='die_y', color='resistance'), rows)
    assert points(figure) == [(1, 2), (2, 2)]
    assert figure['data'][0]['marker']['color'] == [5, 4]
    assert 'resistance' in source and 'groupby' in source


@pytest.mark.parametrize(('aggregate', 'expected'), [
    ('count', 3), ('sum', 10), ('avg', 5), ('min', 2), ('max', 8),
])
def test_wafer_cell_aggregates_keep_sql_null_semantics(aggregate, expected):
    rows = [{'die_x': 1, 'die_y': 2, 'resistance': value, 'wafer_id': 1}
            for value in [2, 8, None]]
    figure, _ = execute(spec('wafer', x='die_x', y='die_y', color='resistance', aggregate=aggregate), rows)
    assert figure['data'][0]['marker']['color'] == [expected]


def test_wafer_default_tested_yield_excludes_untested_rows():
    rows = [{'die_x': 1, 'die_y': 2, 'wafer_id': 1, 'passed': True, 'status': 'tested'},
            {'die_x': 1, 'die_y': 2, 'wafer_id': 1, 'passed': False, 'status': 'tested'},
            {'die_x': 1, 'die_y': 2, 'wafer_id': 1, 'passed': False, 'status': 'untested'}]
    figure, _ = execute(spec('wafer', x='die_x', y='die_y'), rows)
    assert figure['data'][0]['marker']['color'] == [50]


def test_numeric_color_is_original_measurement_scale_across_panels():
    rows = [{'batch': 1, 'reading': 2, 'temperature': 10, 'wafer': 'W1'},
            {'batch': 2, 'reading': 3, 'temperature': 20, 'wafer': 'W2'}]
    figure, _ = execute(spec('scatter', color='temperature', trellis='wafer'), rows, numeric_color=True)
    first, second = figure['data']
    assert first['marker']['color'] == [10] and second['marker']['color'] == [20]
    assert first['marker']['cmin'] == second['marker']['cmin'] == 10
    assert first['marker']['cmax'] == second['marker']['cmax'] == 20


def test_numeric_color_with_all_null_values_still_renders_coordinates():
    rows = [{'batch': 1, 'reading': 2, 'temperature': None},
            {'batch': 2, 'reading': 4, 'temperature': None}]
    figure, _ = execute(spec('scatter', color='temperature'), rows, numeric_color=True)
    assert points(figure) == [(1, 2), (2, 4)]


@pytest.mark.parametrize('kind', ['bar', 'line', 'scatter', 'box', 'histogram', 'wafer', 'kpi'])
def test_empty_dataset_stays_renderable(kind):
    card = spec(kind, aggregate='avg' if kind == 'kpi' else 'none',
                color='reading' if kind == 'wafer' else None)
    figure, _ = execute(card, [], columns=schema([], ['batch', 'reading', 'wafer_id']))
    if kind == 'kpi':
        assert figure['data'][0].get('value') is None
    else:
        assert figure['data'] == []


def test_custom_dataset_source_is_exact_and_legacy_style_migration_preserves_result():
    rows = [{'batch': 'A', 'reading': 2}, {'batch': 'A', 'reading': 4}]
    card = spec(aggregate='sum', editor_mode='python', script_input='dataset',
                python_script='fig=px.violin(df,x="batch",y="reading")')
    assert expanded_dataset_script(card, schema(rows))[0] == card['python_script']
    legacy = simple_script(card) + '\nfig.update_traces(opacity=0.42)'
    card.update(script_input='chart', python_script=legacy)
    migrated, _ = expanded_dataset_script(card, schema(rows))
    assert migrated != legacy and 'groupby' in migrated
    figure = run_script(migrated, rows, schema(rows))
    assert points(figure) == [('A', 6)]
    assert figure['data'][0]['opacity'] == 0.42
    # Each card receives its own df, so visible in-script transformations cannot
    # alter the dataset rows received by a later card.
    fresh = 'fig = go.Figure(go.Bar(x=df["batch"].tolist(), y=df["reading"].tolist()))'
    assert points(run_script(fresh, rows, schema(rows))) == [('A', 2), ('A', 4)]


def test_legacy_migration_avoids_user_variable_collision_and_preserves_korean_literals():
    rows = [{'batch': 'A', 'reading': 2}, {'batch': 'A', 'reading': 4}]
    original = ('# df 문자열과 주석은 바뀌지 않습니다\n'
                '설명 = "한글 df"; chart_data = df.copy()\n'
                'fig = go.Figure(go.Bar(x=chart_data["x"].tolist(), y=chart_data["y"].tolist()))\n'
                'fig.update_layout(title=설명)')
    card = spec(aggregate='sum', editor_mode='python', script_input='chart', python_script=original)
    migrated, _ = expanded_dataset_script(card, schema(rows))
    assert '# df 문자열과 주석은 바뀌지 않습니다' in migrated and '"한글 df"' in migrated
    assert 'chart_data_2' in migrated
    figure = run_script(migrated, rows, schema(rows))
    assert points(figure) == [('A', 6)]
    assert figure['layout']['title']['text'] == '한글 df'


def test_numeric_color_auto_inference_matches_explicit_source_and_legacy_migration():
    rows = [{'batch': 1, 'reading': 2, 'temperature': 10},
            {'batch': 2, 'reading': 4, 'temperature': 20}]
    card = spec('scatter', color='temperature')
    assert dataset_script(card, schema(rows)) == dataset_script(card, schema(rows), numeric_color=True)
    card.update(editor_mode='python', script_input='chart', python_script='fig.update_traces(marker_size=9)')
    migrated, _ = expanded_dataset_script(card, schema(rows))
    figure = run_script(migrated, rows, schema(rows))
    assert len(figure['data']) == 1
    assert figure['data'][0]['marker']['color'] == [10, 20]
    assert figure['data'][0]['marker']['size'] == 9


def test_all_rows_are_streamed_with_original_columns_and_no_hidden_aggregation(monkeypatch):
    card = CardSpec.model_validate({'id': 'all', 'title': 'All', 'dataset_id': 'isolated',
        'dataset_version': 1, 'kind': 'bar', 'x': 'batch', 'y': 'reading', 'aggregate': 'sum',
        'editor_mode': 'python', 'script_input': 'dataset', 'python_script': 'fig=go.Figure()'})
    columns = [{'name': 'batch', 'type': 'text'}, {'name': 'reading', 'type': 'integer'},
               {'name': 'x', 'type': 'integer'}]
    compiled = {'columns': columns}
    original = ({'batch': 'A', 'reading': 1, 'x': i} for i in range(25001))
    queries, calls = [], []
    monkeypatch.setattr(query, 'apply_period', lambda c, *args: (c, None))
    monkeypatch.setattr(query, '_dataset_cte', lambda *args, **kw: ('WITH filtered AS (SELECT 1)', {}, []))
    monkeypatch.setattr(query, 'stream_rows', lambda sql, params: queries.append(sql) or original)
    def execute(script, rows, cols):
        assert rows is original and cols == columns
        consumed = list(rows)
        calls.append(consumed)
        return plotly_scripts.ScriptResult({'data': []}, len(consumed), 100, 1, 1, 1)
    monkeypatch.setattr(plotly_scripts, 'execute_script', execute)
    result = query._compute_script_card(card, QueryContext(), compiled)
    assert calls[0][-1]['x'] == 25000
    assert result['rows'] == [] and result['meta']['input_rows'] == 25001
    assert result['meta']['returned_rows'] == 25001
    assert result['meta']['sampled'] is False and result['meta']['truncated'] is False
    assert all(word not in queries[0] for word in ('LIMIT', 'ORDER BY', 'GROUP BY', 'SUM('))
