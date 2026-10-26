"""Execute cell-value queries against isolated rows; never change saved cards."""
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import query
from app.schemas import CardSpec, QueryContext


@pytest.fixture
def cell_query(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE cells (wafer_id integer, die_x integer, die_y integer,
                            reading real, status text, passed boolean);
        INSERT INTO cells VALUES (1, 2, 3, 10, 'tested', 1),
                                 (1, 2, 3, 30, 'tested', 0),
                                 (1, 4, 5, 8, 'untested', NULL);
    """)
    columns = [{'name': name, 'type': dtype} for name, dtype in
               [('wafer_id', 'integer'), ('die_x', 'integer'), ('die_y', 'integer'),
                ('reading', 'real'), ('status', 'text'), ('passed', 'boolean')]]
    dataset = SimpleNamespace(columns=columns, spec={}, project_id=None)
    monkeypatch.setattr(query, 'validate_dataset', lambda spec: {})
    monkeypatch.setattr(query.database, 'workspace_token', lambda: 'isolated-cell-test')
    monkeypatch.setattr(query, '_dataset_cte', lambda *args: ('WITH filtered AS (SELECT * FROM cells)', {}, []))
    monkeypatch.setattr(query, 'execute_rows', lambda sql, params: [dict(row) for row in conn.execute(
        sql, {key: float(value) if isinstance(value, Decimal) else value for key, value in params.items()})])

    def run(**changes):
        card = CardSpec.model_validate(dict(id='cell-test', title='Cell test', dataset_id='isolated',
                                           dataset_version=1, kind='wafer', x='die_x', y='die_y',
                                           aggregate='none') | changes)
        return query.chart_query(card, QueryContext(), dataset=dataset)

    yield run, conn, dataset
    conn.close()


@pytest.mark.parametrize('aggregate,expected', [('avg', 20), ('sum', 40), ('min', 10), ('max', 30), ('none', 20)])
def test_wafer_uses_selected_cell_column_without_changing_coordinates(cell_query, aggregate, expected):
    run, _, _ = cell_query
    result = run(color='reading', aggregate=aggregate)
    assert result['rows'] == [{'x': 2, 'y': 3, 'value': expected}, {'x': 4, 'y': 5, 'value': 8}]
    assert result['meta']['value_label'] == 'reading'


def test_existing_wafer_inspection_yield_and_explicit_row_count_are_distinct(cell_query):
    run, _, _ = cell_query
    legacy = run()
    assert [r['value'] for r in legacy['rows']] == [50, None]
    assert legacy['meta']['value_label'] == '검사 수율 (%)'
    count = run(aggregate='count')
    assert [r['value'] for r in count['rows']] == [2, 1]
    assert count['meta']['value_label'] == '셀별 행 개수'


def test_wafer_without_inspection_columns_keeps_legacy_count(cell_query):
    run, _, dataset = cell_query
    dataset.columns = [c for c in dataset.columns if c['name'] not in {'passed', 'status'}]
    assert [r['value'] for r in run()['rows']] == [2, 1]


@pytest.mark.parametrize('changes', [{'aggregate': 'avg'}, {'color': 'status'}, {'color': 'missing'}])
def test_wafer_rejects_missing_or_non_numeric_cell_values(cell_query, changes):
    run, _, _ = cell_query
    with pytest.raises(HTTPException) as error:
        run(**changes)
    assert error.value.status_code == 422


def test_selecting_cell_data_keeps_single_wafer_guard(cell_query):
    run, conn, _ = cell_query
    conn.execute("INSERT INTO cells VALUES (2, 2, 3, 99, 'tested', 1)")
    with pytest.raises(HTTPException, match='하나의 wafer'):
        run(color='reading', aggregate='avg')


def test_wafer_panels_never_merge_repeated_coordinates(cell_query):
    run, conn, _ = cell_query
    conn.execute("INSERT INTO cells VALUES (2, 2, 3, 99, 'tested', 1)")
    result = run(color='reading', aggregate='avg', trellis='wafer_id')
    assert {(r['trellis'], r['x'], r['y']): r['value'] for r in result['rows']} == {
        (1, 2, 3): 20, (1, 4, 5): 8, (2, 2, 3): 99}
    result = run(trellis='wafer_id')
    assert {(r['trellis'], r['x']): r['value'] for r in result['rows']} == {(1, 2): 50, (1, 4): None, (2, 2): 100}
    expression = [{'kind': 'column', 'name': 'reading'}, {'kind': 'operator', 'value': '*'}, {'kind': 'number', 'value': '2'}]
    result = run(trellis='wafer_id', aggregate='avg', value_expression=expression)
    assert {(r['trellis'], r['x']): r['value'] for r in result['rows']} == {(1, 2): 40, (1, 4): 16, (2, 2): 198}


def test_wafer_rejects_mixed_panel_even_with_null_wafer(cell_query):
    run, conn, _ = cell_query
    assert len(run(trellis='status')['rows']) == 2
    for wafer in (2, None):
        conn.execute("INSERT INTO cells VALUES (?, 2, 3, 99, 'tested', 1)", (wafer,))
        with pytest.raises(HTTPException, match='각 패널에는 하나의 wafer'):
            run(trellis='status')
        conn.execute('DELETE FROM cells WHERE wafer_id IS NULL OR wafer_id = 2')


def test_wafer_panel_limit_is_enforced_before_drawing(cell_query):
    run, conn, _ = cell_query
    conn.executemany("INSERT INTO cells VALUES (?, 2, 3, 99, 'tested', 1)", [(n,) for n in range(2, 14)])
    with pytest.raises(HTTPException, match='최대 12개'):
        run(trellis='wafer_id')


def test_legacy_saved_renderer_opens_interactively():
    card = CardSpec(id='legacy', title='Legacy', dataset_id='test', dataset_version=1, kind='bar', renderer='seaborn')
    assert card.renderer == 'plotly'
    assert CardSpec.model_validate_json(card.model_dump_json()).renderer == 'plotly'
