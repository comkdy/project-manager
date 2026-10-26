"""Generated card source must build the entire plot without a hidden base figure."""
from copy import deepcopy

import pytest

from app.plotly_codegen import LEGACY_SCRIPT, expanded_script, input_columns, simple_script
from app.plotly_scripts import run_script
from test_integration import api, dataset


def card(kind='scatter', **updates):
    return {'kind': kind, 'title': 'Measurements', 'x': 'lot', 'y': 'measurement',
            'aggregate': 'none', 'color': None, 'trellis': None, 'options': {}, **updates}


def execute(spec, rows, *, numeric_color=False):
    # Deliberately omit run_script's base_card argument. The displayed source
    # has to construct every trace itself, using only the supplied query rows.
    return run_script(simple_script(spec, numeric_color=numeric_color), rows, input_columns(spec))


@pytest.mark.parametrize(('kind', 'expected_type', 'extra', 'rows'), [
    ('bar', 'bar', {}, [{'x': 'A', 'y': 7}, {'x': 'B', 'y': 4}]),
    ('scatter', 'scatter', {}, [{'x': 1, 'y': 7}, {'x': 2, 'y': 4}]),
    ('line', 'scatter', {}, [{'x': 1, 'y': 7}, {'x': 2, 'y': 4}]),
    ('histogram', 'bar', {}, [{'x': 0.5, 'y': 7}, {'x': 1.5, 'y': 4}]),
    ('box', 'box', {}, [{'x': 'A', 'y': 7}, {'x': 'A', 'y': 4}]),
    ('wafer', 'scatter', {'color': 'measurement'}, [{'x': 1, 'y': 2, 'value': 7}, {'x': 2, 'y': 1, 'value': 4}]),
    ('kpi', 'indicator', {'aggregate': 'avg'}, [{'value': 5.5}]),
])
def test_every_simple_kind_builds_standalone_figure(kind, expected_type, extra, rows):
    spec = card(kind, **extra)
    result = execute(spec, rows)
    assert len(result['data']) == 1
    trace = result['data'][0]
    assert trace['type'] == expected_type
    if kind == 'kpi':
        assert trace['value'] == 5.5
    else:
        assert trace['x'] == [r['x'] for r in rows]
        assert trace['y'] == [r['y'] for r in rows]
        if kind == 'line':
            assert trace['mode'] == 'lines+markers'
        if kind == 'wafer':
            assert trace['marker']['symbol'] == 'square'
            assert trace['marker']['color'] == [7, 4]
            assert result['layout']['yaxis']['scaleanchor'] == 'x'


def test_categories_palette_axis_labels_and_log_ranges_survive_conversion():
    spec = card(color='batch', options={'palette': 'warm', 'x_label': 'Elapsed hours',
        'y_label': 'Resistance', 'x_scale': 'log', 'x_min': 1, 'x_max': 100,
        'y_min': 2, 'y_max': 20, 'x_font_size': 13, 'y_font_size': 12})
    rows = [{'x': 1, 'y': 5, 'color': 'A'}, {'x': 10, 'y': 7, 'color': 'B'},
            {'x': 100, 'y': 9, 'color': 'A'}]
    result = execute(spec, rows)
    assert [(t['name'], t['x']) for t in result['data']] == [('A', [1, 100]), ('B', [10])]
    assert [t['marker']['color'] for t in result['data']] == ['#ba6b45', '#e8a05c']
    assert all(t['showlegend'] for t in result['data'])
    x, y = result['layout']['xaxis'], result['layout']['yaxis']
    assert x['title']['text'] == 'Elapsed hours' and x['range'] == [0, 2] and x['type'] == 'log'
    assert y['title']['text'] == 'Resistance' and y['range'] == [2, 20]
    assert x['tickfont']['size'] == 13 and y['tickfont']['size'] == 12


def test_numeric_color_uses_one_continuous_scale_across_trellis_panels():
    spec = card(color='temperature', trellis='wafer', options={'palette': 'viridis', 'trellis_columns': 2})
    rows = [{'x': 1, 'y': 2, 'color': 10, 'trellis': 'W1'},
            {'x': 2, 'y': 3, 'color': 20, 'trellis': 'W2'},
            {'x': 3, 'y': 4, 'color': 30, 'trellis': 'W2'}]
    result = execute(spec, rows, numeric_color=True)
    first, second = result['data']
    assert first['marker']['color'] == [10] and second['marker']['color'] == [20, 30]
    assert first['marker']['cmin'] == second['marker']['cmin'] == 10
    assert first['marker']['cmax'] == second['marker']['cmax'] == 30
    assert first['marker']['showscale'] and not second['marker']['showscale']
    assert second['xaxis'] == 'x2'
    assert [a['text'] for a in result['layout']['annotations']] == ['W1', 'W2']


def test_categorical_trellis_includes_missing_panel_and_keeps_groups_separate():
    spec = card('bar', color='batch', trellis='wafer')
    rows = [{'x': 'L1', 'y': 2, 'color': 'A', 'trellis': 'W1'},
            {'x': 'L2', 'y': 3, 'color': 'A', 'trellis': None},
            {'x': 'L3', 'y': 4, 'color': 'B', 'trellis': None}]
    result = execute(spec, rows)
    assert len(result['data']) == 3
    assert [t['y'] for t in result['data']] == [[2], [3], [4]]
    assert [a['text'] for a in result['layout']['annotations']] == ['W1', '(빈 값)']
    assert [(t['name'], t['showlegend']) for t in result['data']] == [('A', True), ('A', False), ('B', True)]


def test_numeric_categories_stay_categorical_for_bar_and_legacy_modifier():
    spec = card('bar', color='wafer')
    rows = [{'x': 'A', 'y': 2, 'color': 1}, {'x': 'B', 'y': 3, 'color': 2}]
    # Type inference may classify the data as numeric, but continuous color
    # is a scatter-only feature: wafer IDs on bar charts remain categories.
    result = execute(spec, rows, numeric_color=True)
    assert [(t['name'], t['y']) for t in result['data']] == [('1', [2]), ('2', [3])]
    legacy = run_script('fig.update_traces(opacity=0.42)', rows, input_columns(spec), spec)
    assert [(t['name'], t['y']) for t in legacy['data']] == [('1', [2]), ('2', [3])]
    assert all(t['opacity'] == 0.42 for t in legacy['data'])


@pytest.mark.parametrize('kind', ['bar', 'scatter', 'line', 'histogram', 'box', 'wafer', 'kpi'])
def test_empty_query_results_render_without_missing_column_errors(kind):
    spec = card(kind, color='measurement' if kind == 'wafer' else 'batch',
                trellis=None if kind == 'kpi' else 'wafer')
    result = execute(spec, [])
    if kind == 'kpi':
        assert result['data'][0]['type'] == 'indicator'
        assert result['data'][0].get('value') is None
    else:
        assert result['data'] == []


def test_legacy_modifiers_expand_but_explicit_user_figures_remain_exact():
    spec = card('bar', editor_mode='python', python_script=LEGACY_SCRIPT)
    migrated, baseline = expanded_script(spec)
    assert migrated == baseline and 'fig = go.Figure()' in migrated
    spec['python_script'] = 'fig.update_traces(opacity=0.42)\nfig.update_layout(title="My chart")'
    migrated, baseline = expanded_script(spec)
    assert migrated.startswith(baseline) and migrated.endswith(spec['python_script'])
    result = run_script(migrated, [{'x': 'A', 'y': 2}], input_columns(spec))
    assert result['data'][0]['opacity'] == 0.42
    assert result['layout']['title']['text'] == 'My chart'
    for original in ['fig = go.Figure(go.Pie(labels=["A"],values=[2]))', 'fig = go.Figure( syntax error']:
        spec['python_script'] = original
        assert expanded_script(spec)[0] == original


def test_full_script_ignores_legacy_hidden_base_and_reads_latest_rows():
    spec = card('bar')
    script = simple_script(spec)
    original = [{'x': 'A', 'y': 10}]
    latest = [{'x': 'A', 'y': 30}, {'x': 'B', 'y': 40}]
    before = run_script(script, original, input_columns(spec))
    # A mismatched base card would fail/look different if the worker still
    # rendered hidden instructions before running this complete script.
    after = run_script(script, latest, input_columns(spec), card('wafer', color='missing'))
    assert before['data'][0]['y'] == [10]
    assert after['data'][0]['y'] == [30, 40]
    assert after['data'][0]['type'] == 'bar'
    assert '30' not in script and '40' not in script


def test_column_label_cannot_inject_python_into_generated_source():
    spec = card(x='lot\nfig = go.Figure(go.Pie(values=[999]))\n#')
    result = execute(spec, [{'x': 'A', 'y': 2}])
    assert result['data'][0]['type'] == 'scatter'
    assert result['data'][0]['y'] == [2]


def test_input_column_mapping_uses_real_columns_not_display_labels():
    spec = card('bar', aggregate='avg', options={'x_label': 'Batch', 'y_label': 'Average'})
    assert input_columns(spec) == [{'name': 'x', 'description': 'lot'},
                                   {'name': 'y', 'description': 'AVG(measurement)'}]
    wafer = card('wafer', x='die_x', y='die_y', color='resistance', aggregate='avg')
    assert input_columns(wafer)[-1] == {'name': 'value', 'description': 'AVG(resistance)'}


def test_conversion_api_returns_complete_readonly_script_and_filtered_preview(api):
    ds = dataset(api, 'inline_measurements')
    spec = {'id': 'qa-codegen', 'title': 'By measurement', 'kind': 'bar',
            'dataset_id': ds['id'], 'dataset_version': ds['version'],
            'x': 'metric_name', 'y': 'value', 'aggregate': 'avg',
            'filters': [{'column': 'wafer_id', 'op': 'eq', 'value': 1}],
            'period': {'use_dashboard': False}, 'options': {'palette': 'warm'}}
    board_before = api.get('/api/dashboards?project_id=1').json()
    catalog_before = api.get('/api/bootstrap').json()['datasets']
    response = api.post('/api/cards/python-script', json={'card': spec, 'context': {'project_id': 1}})
    assert response.status_code == 200, response.text
    conversion = response.json()
    assert conversion['card']['editor_mode'] == 'python'
    assert conversion['card']['script_input'] == 'dataset'
    assert conversion['card']['python_script'] == conversion['simple_script']
    assert 'go.Bar(' in conversion['simple_script'] and 'df.groupby(' in conversion['simple_script']
    assert 'import ' not in conversion['simple_script'] and '.copy()' not in conversion['simple_script']
    assert [c['name'] for c in conversion['input_columns']] == [c['name'] for c in ds['columns']]
    assert all(c['description'] == c['name'] for c in conversion['input_columns'])
    assert 'groupby' in conversion['simple_script']
    expected = api.post('/api/cards/query', json={'card': spec, 'context': {'project_id': 1}}).json()['rows']
    preview = api.post('/api/cards/query', json={'card': conversion['card'], 'context': {'project_id': 1}})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result['rows'] == [] and result['meta']['input_rows'] > 0
    actual = dict(zip(result['figure']['data'][0]['x'], result['figure']['data'][0]['y']))
    assert actual == pytest.approx({r['x']: r['y'] for r in expected})
    empty_card = deepcopy(conversion['card'])
    empty_card['filters'] = [{'column': 'metric_name', 'op': 'eq', 'value': '__qa_nonexistent__'}]
    empty = api.post('/api/cards/query', json={'card': empty_card, 'context': {'project_id': 1}})
    assert empty.status_code == 200, empty.text
    assert empty.json()['rows'] == [] and empty.json()['figure']['data'] == []
    assert api.get('/api/dashboards?project_id=1').json() == board_before
    assert api.get('/api/bootstrap').json()['datasets'] == catalog_before


def test_conversion_api_does_not_change_custom_dataset_script_or_df_columns(api):
    ds = dataset(api, 'inline_measurements')
    script = 'fig=px.violin(df,x="metric_name",y="value")\nfig.update_traces(opacity=0.55)'
    original = {'id': 'qa-custom-codegen', 'title': 'Custom violin', 'kind': 'box',
                'dataset_id': ds['id'], 'dataset_version': ds['version'],
                'editor_mode': 'python', 'script_input': 'dataset', 'python_script': script,
                'period': {'use_dashboard': False},
                'filters': [{'column': 'wafer_id', 'op': 'eq', 'value': 1}]}
    # There are intentionally no simple-editor x/y settings. This is already
    # a complete script referring to the dataset's actual output columns.
    converted = api.post('/api/cards/python-script', json={'card': original, 'context': {'project_id': 1}})
    assert converted.status_code == 200, converted.text
    restored = converted.json()['card']
    assert restored['python_script'] == script
    assert restored['script_input'] == 'dataset'
    assert restored['x'] is None and restored['y'] is None
    result = api.post('/api/cards/query', json={'card': restored, 'context': {'project_id': 1}})
    assert result.status_code == 200, result.text
    assert result.json()['figure']['data'][0]['type'] == 'violin'
    assert result.json()['figure']['data'][0]['opacity'] == 0.55
    assert result.json()['rows'] == [] and result.json()['meta']['input_rows'] > 0
