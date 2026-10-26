"""All-row transport, bounded parent buffering, cleanup and source isolation."""
from pathlib import Path
from threading import BoundedSemaphore

import pytest
from fastapi import HTTPException

from app import plotly_scripts as runner
from app.plotly_policy import BATCH_ROWS
from app.query import stream_rows
from test_integration import api


def test_more_than_10000_rows_and_eight_mb_are_not_truncated():
    count = 25001
    rows = ({'reading': 1000000 if i == count - 1 else 1, 'label': '측정값' * 160}
            for i in range(count))
    columns = [{'name': 'reading', 'type': 'integer'}, {'name': 'label', 'type': 'text'}]
    script = f'require(len(df) == {count}, "missing rows")\nfig=go.Figure(go.Indicator(mode="number",value=df["reading"].sum()))'
    result = runner.execute_script(script, rows, columns)
    assert result.input_rows == count and result.input_bytes > 8 * 1024 * 1024
    assert result.figure['data'][0]['value'] == 1025000


def test_batch_boundaries_keep_nulls_types_and_original_column_names():
    rows = ({'측정 값': None if i < BATCH_ROWS else i, 'lot_cd': 'XXXA' if i % 2 else None}
            for i in range(BATCH_ROWS + 3))
    script = ('require(len(df) == 2051, "missing rows")\n'
              'require(df["측정 값"].count() == 3, "nulls lost")\n'
              'require(df["lot_cd"].nunique() == 1, "labels changed")\n'
              'fig=go.Figure(go.Indicator(mode="number",value=df["측정 값"].sum()))')
    result = runner.execute_script(script, rows, [{'name': '측정 값'}, {'name': 'lot_cd'}])
    assert result.figure['data'][0]['value'] == 6147


def test_busy_slot_never_opens_or_buffers_a_source_query(monkeypatch):
    slots = BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(runner, '_slots', slots)
    monkeypatch.setattr(runner, 'QUEUE_TIMEOUT', 0.01)
    touched = []
    def rows():
        touched.append(True)
        yield {'reading': 1}
    with pytest.raises(HTTPException) as error:
        runner.execute_script('fig=go.Figure()', rows(), [{'name': 'reading'}])
    assert error.value.status_code == 429 and not touched
    slots.release()


def test_stream_failure_closes_cursor_cleans_spool_and_releases_slot(monkeypatch, tmp_path):
    real_tempdir = runner.tempfile.TemporaryDirectory
    monkeypatch.setattr(runner.tempfile, 'TemporaryDirectory', lambda **kw: real_tempdir(dir=tmp_path, **kw))
    monkeypatch.setattr(runner, '_slots', BoundedSemaphore(1))
    closed = []
    def failed_rows():
        try:
            yield from ({'reading': i} for i in range(BATCH_ROWS))
            raise HTTPException(408, 'source failed')
        finally:
            closed.append(True)
    with pytest.raises(HTTPException, match='source failed'):
        runner.execute_script('fig=go.Figure()', failed_rows(), [{'name': 'reading'}])
    assert closed and list(tmp_path.iterdir()) == []
    assert runner.run_script('fig=go.Figure()', [], [{'name': 'reading'}])['data'] == []
    assert list(tmp_path.iterdir()) == []


def test_storage_pressure_never_executes_a_partial_input(monkeypatch, tmp_path):
    from types import SimpleNamespace
    closed = []
    def rows():
        try:
            yield {'reading': 1}
        finally:
            closed.append(True)
    monkeypatch.setattr(runner.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(HTTPException, match='임시 저장 공간'):
        runner._spool_rows(tmp_path, rows(), [{'name': 'reading'}])
    assert closed


def test_real_readonly_cursor_delivers_every_row(api):
    rows = stream_rows('SELECT n AS reading FROM generate_series(1,15001) AS n')
    result = runner.execute_script('fig=go.Figure(go.Indicator(mode="number",value=df["reading"].sum()))',
                                   rows, [{'name': 'reading', 'type': 'integer'}])
    assert result.input_rows == 15001
    assert result.figure['data'][0]['value'] == 15001 * 15002 / 2
    # The cursor uses the existing read-only connection policy, not new DB credentials.
    assert list(stream_rows("SELECT current_setting('transaction_read_only')")) == [('on',)]
