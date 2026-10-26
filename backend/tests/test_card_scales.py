from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.query import validate_card
from app.rendering import draw_chart
from app.schemas import CardOptions, CardSpec


def card(**changes):
    return CardSpec(**dict(dict(id='log', title='Log scale', dataset_id='data', dataset_version=1,
                               kind='scatter', x='value', y='value', aggregate='none'), **changes))


def test_scale_defaults_and_persistence():
    assert CardOptions().x_scale == CardOptions().y_scale == 'linear'
    saved = card(options=CardOptions(x_scale='log', y_scale='log', x_min=.1, x_max=1000))
    restored = CardSpec.model_validate_json(saved.model_dump_json())
    assert restored.options.x_scale == restored.options.y_scale == 'log'
    assert restored.options.x_min == .1 and restored.options.x_max == 1000


@pytest.mark.parametrize('options', [dict(x_scale='ln'), dict(y_scale='sqrt'),
                                    dict(x_scale='log', x_min=0), dict(y_scale='log', y_max=-1),
                                    dict(x_scale='log', x_max=float('inf'))])
def test_invalid_scale_settings_are_rejected(options):
    with pytest.raises(ValidationError):
        CardOptions(**options)


@pytest.fixture
def dataset(monkeypatch):
    result = SimpleNamespace(columns=[dict(name='value', type='numeric'), dict(name='group', type='text'),
                                      dict(name='time', type='timestamp')], spec={}, project_id=None)
    monkeypatch.setattr('app.query.validate_dataset', lambda spec: {})
    return result


@pytest.mark.parametrize('kind', ['scatter', 'line', 'histogram', 'bar', 'box', 'wafer', 'kpi'])
@pytest.mark.parametrize('axis', ['x', 'y'])
def test_chart_axis_eligibility_is_enforced_by_api(dataset, kind, axis):
    supported = kind in (['scatter', 'line', 'histogram'] if axis == 'x' else ['scatter', 'line', 'histogram', 'bar', 'box'])
    if supported:
        value = card(kind=kind, options=CardOptions(**{f'{axis}_scale':'log'}))
        validate_card(value, dataset)
    else:
        with pytest.raises((HTTPException, ValidationError)):
            validate_card(card(kind=kind, options=CardOptions(**{f'{axis}_scale':'log'})), dataset)


@pytest.mark.parametrize('x', ['group', 'time'])
def test_text_and_date_axes_cannot_be_logarithmic(dataset, x):
    with pytest.raises(HTTPException, match='X축'):
        validate_card(card(x=x, options=CardOptions(x_scale='log')), dataset)


def test_count_y_is_numeric_without_a_value_column(dataset):
    validate_card(card(kind='bar', x='group', y=None, aggregate='count', options=CardOptions(y_scale='log')), dataset)
    with pytest.raises(HTTPException, match='Y축'):
        validate_card(card(kind='line', y='group', aggregate='min', options=CardOptions(y_scale='log')), dataset)


@pytest.mark.parametrize('kind', ['scatter', 'line', 'histogram', 'bar', 'box'])
def test_exports_apply_the_same_scale_and_real_value_limits(monkeypatch, kind):
    from matplotlib.figure import Figure
    captured = {}
    original = Figure.savefig

    def inspect(figure, *args, **kwargs):
        ax = figure.axes[0]
        captured.update(x_scale=ax.get_xscale(), y_scale=ax.get_yscale(), y_lim=ax.get_ylim())
        if kind == 'histogram':
            captured['centres'] = [patch.get_x() + patch.get_width()/2 for patch in ax.patches]
        return original(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    x_scale = 'log' if kind in {'scatter', 'line', 'histogram'} else 'linear'
    value = card(kind=kind, options=CardOptions(x_scale=x_scale, y_scale='log', y_min=1, y_max=1000))
    svg = draw_chart({'card':value.model_dump(), 'result':{'rows':[dict(x=1,y=1),dict(x=10,y=10),dict(x=100,y=100)]}}, 'svg')
    assert svg.startswith(b'<?xml')
    assert captured['x_scale'] == x_scale and captured['y_scale'] == 'log'
    assert captured['y_lim'] == (1, 1000)
    if kind == 'histogram':
        assert captured['centres'] == pytest.approx([1, 10, 100])


def test_nonpositive_data_does_not_crash_log_export():
    value = card(options=CardOptions(x_scale='log', y_scale='log'))
    svg = draw_chart({'card':value.model_dump(), 'result':{'rows':[dict(x=0,y=-2),dict(x=-1,y=0)]}}, 'svg')
    assert svg.startswith(b'<?xml')
