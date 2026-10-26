import re

import pytest
from pydantic import ValidationError

from app.rendering import draw_chart
from app.schemas import CardOptions, CardSpec


def test_font_sizes_are_backwards_compatible_and_survive_serialization():
    card = CardSpec(id='preview', title='Font check', dataset_id='data', dataset_version=1,
                    kind='bar', x='group', y='value', aggregate='avg')
    assert card.options.x_font_size == card.options.y_font_size == 10
    card.options = CardOptions(x_font_size=18, y_font_size=24)
    restored = CardSpec.model_validate_json(card.model_dump_json())
    assert restored.options.x_font_size == 18
    assert restored.options.y_font_size == 24


@pytest.mark.parametrize('options', [{'x_font_size': 7}, {'y_font_size': 33}, {'x_font_size': 12.5}])
def test_font_sizes_reject_out_of_range_values(options):
    with pytest.raises(ValidationError):
        CardOptions(**options)


def test_export_renders_both_axis_font_sizes():
    card = CardSpec(id='preview', title='Font check', dataset_id='data', dataset_version=1,
                    kind='bar', x='Group', y='Value', aggregate='avg',
                    options=CardOptions(x_font_size=18, y_font_size=24))
    svg = draw_chart({'card':card.model_dump(), 'result':{'rows':[{'x':'A','y':1},{'x':'B','y':2}]}}, 'svg').decode()
    assert re.search(r'<text[^>]*font-size: 18px[^>]*>Group</text>', svg)
    assert re.search(r'<text[^>]*font-size: 24px[^>]*>Value</text>', svg)


def test_axis_display_names_are_optional_and_survive_serialization():
    assert CardOptions().x_label is None and CardOptions().y_label is None
    options = CardOptions(x_label=' 랏코드 ', y_label='  ')
    restored = CardOptions.model_validate_json(options.model_dump_json())
    assert restored.x_label == '랏코드' and restored.y_label is None
    with pytest.raises(ValidationError):
        CardOptions(x_label='a' * 81)


@pytest.mark.parametrize('kind', ['bar', 'line', 'scatter', 'wafer'])
def test_export_renders_custom_axis_names_without_changing_columns(kind):
    card = CardSpec(id='axis', title='Named axes', dataset_id='data', dataset_version=1,
                    kind=kind, x='lot_code', y='value', aggregate='avg',
                    options=CardOptions(x_label='랏코드', y_label='측정값'))
    svg = draw_chart({'card':card.model_dump(), 'result':{'rows':[{'x':1,'y':2,'value':90}]}}, 'svg').decode()
    assert '>랏코드</text>' in svg and '>측정값</text>' in svg
    assert '>lot_code</text>' not in svg and '>value</text>' not in svg
    assert card.x == 'lot_code' and card.y == 'value'


@pytest.mark.parametrize('kind', ['kpi', 'bar', 'wafer'])
def test_export_uses_expression_name_instead_of_formula(kind):
    card = CardSpec(id='alias', title='Named value', dataset_id='data', dataset_version=1,
                    kind=kind, x='X', y='Y' if kind in {'wafer'} else None, aggregate='avg',
                    value_expression=[{'kind':'column', 'name':'very_long_column_name'}],
                    value_expression_name='수율')
    svg = draw_chart({'card':card.model_dump(), 'result':{'rows':[{'x':1,'y':2,'value':90}],
                     'meta':{'value_label':'very_long_column_name'}}}, 'svg').decode()
    assert '>수율</text>' in svg
    assert 'very_long_column_name' not in svg
