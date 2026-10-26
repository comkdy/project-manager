import json

import pytest

from app.assistant import selected_card_python_source
from app.plotly_codegen import LEGACY_SCRIPT, dataset_script, expanded_dataset_script
from app.schemas import CardSpec
from test_chat_workflow import events, mock_llm
from test_integration import api, dataset


def selected(**updates):
    return CardSpec.model_validate({'id': 'selected', 'title': 'Chart', 'kind': 'scatter',
        'dataset_id': 'measurements', 'dataset_version': 1, 'x': 'sample', 'y': 'value',
        'color': 'temperature', **updates})


CATALOG = [{'id': 'measurements', 'columns': [
    {'name': 'sample', 'type': 'integer'}, {'name': 'value', 'type': 'double precision'},
    {'name': 'temperature', 'type': 'numeric'}, {'name': 'category', 'type': 'text'}]}]


def test_selected_simple_source_uses_catalog_types_and_preserves_card():
    card = selected()
    before = card.model_dump()
    source = selected_card_python_source(card, CATALOG)
    assert source == dataset_script(before, CATALOG[0]['columns'], numeric_color=True)
    assert 'temperature' in source
    assert card.model_dump() == before
    categorical = selected(color='category')
    assert selected_card_python_source(categorical, CATALOG) == dataset_script(categorical.model_dump(), CATALOG[0]['columns'])


def test_selected_legacy_source_expands_modifiers_but_preserves_complete_user_code():
    legacy = selected(editor_mode='python', script_input='chart', python_script=LEGACY_SCRIPT)
    assert selected_card_python_source(legacy, CATALOG) == dataset_script(legacy.model_dump(), CATALOG[0]['columns'], numeric_color=True)
    modifier = 'fig.update_traces(marker_size=9)'
    legacy.python_script = modifier
    assert selected_card_python_source(legacy, CATALOG).endswith(modifier)
    complete = 'fig = go.Figure(go.Pie(labels=["A"], values=[3]))\nfig.update_layout(title="Own chart")'
    legacy.python_script = complete
    migrated = selected_card_python_source(legacy, CATALOG)
    assert migrated == expanded_dataset_script(legacy.model_dump(), CATALOG[0]['columns'], numeric_color=True)[0]
    assert complete in migrated


@pytest.mark.parametrize('card,catalog', [
    (None, CATALOG),
    (selected(kind='gantt'), CATALOG),
    (selected(editor_mode='python', script_input='dataset', python_script='fig=px.scatter(df,x="sample",y="value")'), CATALOG),
    (selected(), []),
    (selected(x='removed_column'), CATALOG),
    (selected(x=None), CATALOG),
    (selected(y=None), CATALOG),
    (selected(aggregate='yield'), CATALOG),
    (selected(), [{'id': 'measurements'}]),
])
def test_inapplicable_or_stale_card_omits_optional_baseline(card, catalog):
    assert selected_card_python_source(card, catalog) is None


def test_chat_passes_complete_selected_source_without_data_rows(api, monkeypatch):
    ds = dataset(api, 'inline_measurements')
    card = {'id': 'selected-python-context', 'title': 'Scatter', 'kind': 'scatter',
            'dataset_id': ds['id'], 'dataset_version': ds['version'],
            'x': 'wafer_id', 'y': 'value', 'color': 'value'}
    observed = mock_llm(monkeypatch, [{'kind': 'route', 'intent': 'card'},
                                     {'kind': 'clarification', 'question': '점 크기를 얼마로 할까요?'}])
    result = events(api.post('/api/chat', json={'message': '점 크기를 수정하고 싶어', 'project_id': 1, 'card': card}))
    assert 'clarification' in result and 'error' not in result
    context = json.loads(observed[-1]['messages'][0]['content'].split('\nRegistered context:\n', 1)[1])
    source = context['selected_card_python_source']
    assert 'fig = go.Figure()' in source and 'go.Scatter(' in source
    assert 'wafer_id' in source and 'value' in source
    assert context['selected_card']['editor_mode'] == 'simple'
    assert context['selected_card']['python_script'] is None
    assert 'rows' not in context and "df['value']" in source
    assert 'import ' not in source and '.copy()' not in source


def test_stale_selected_card_still_allows_assistant_clarification(api, monkeypatch):
    ds = dataset(api, 'inline_measurements')
    card = {'id': 'stale-selected', 'title': 'Old card', 'kind': 'scatter',
            'dataset_id': ds['id'], 'dataset_version': ds['version'],
            'x': 'removed_field', 'y': 'value'}
    observed = mock_llm(monkeypatch, [{'kind': 'route', 'intent': 'card'},
                                     {'kind': 'clarification', 'question': 'X축 컬럼이 변경되었습니다. 어느 컬럼을 사용할까요?'}])
    result = events(api.post('/api/chat', json={'message': '이 차트를 수정해줘', 'project_id': 1, 'card': card}))
    assert 'clarification' in result and 'error' not in result
    context = json.loads(observed[-1]['messages'][0]['content'].split('\nRegistered context:\n', 1)[1])
    assert 'selected_card_python_source' not in context
    assert context['selected_card']['x'] == 'removed_field'
