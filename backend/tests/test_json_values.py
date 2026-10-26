"""Source scalar types share the same JSON contract in cards and worker input."""
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from uuid import UUID

import pytest

from app import query
from app.chart_cache import ChartPayload
from app.plotly_scripts import _spool_rows
from app.schemas import CardSpec, QueryContext


@pytest.mark.parametrize("value, expected", [
    (UUID("12345678-1234-5678-1234-567812345678"), "12345678-1234-5678-1234-567812345678"),
    (time(9, 30, 15), "09:30:15"),
    (time(9, 30, tzinfo=timezone(timedelta(hours=9))), "09:30:00+09:00"),
    (timedelta(days=-1, seconds=30, microseconds=500000), -86369.5),
    (date(2026, 10, 23), "2026-10-23"),
    (datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc), "2026-10-23T09:30:00+00:00"),
    (Decimal("12.5"), 12.5),
    (Decimal("NaN"), None),
])
def test_source_scalars_are_json_safe_in_nested_results(value, expected):
    result = query.clean({"values": [value], "tuple": (value,)})
    assert json.loads(json.dumps(result, allow_nan=False)) == {"values": [expected], "tuple": [expected]}


class SourceRows:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def mappings(self):
        return self.rows

    def partitions(self, size):
        yield [tuple(row.values()) for row in self.rows]


class SourceConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args):
        return SourceRows(self.rows)

    def execution_options(self, **kwargs):
        return self


def test_uuid_category_reaches_fresh_and_cached_chart_responses(monkeypatch):
    identity = UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr(query, "source_connection", lambda: nullcontext(SourceConnection([{"x": identity, "y": 3}])))
    card = CardSpec(id="uuid-card", title="UUID groups", dataset_id="test", dataset_version=1,
                    kind="bar", x="category", aggregate="count")
    compiled = {"direct_sql": "SELECT category FROM source",
                "columns": [{"name": "category", "type": "uuid"}]}
    result = query._compute_chart(card, QueryContext(), compiled)
    payload = ChartPayload.from_result(result)
    for encoded, cached in ((payload.fresh, False), (payload.cached, True)):
        response = json.loads(encoded)
        assert response["rows"] == [{"x": str(identity), "y": 3}]
        assert response["meta"]["cached"] is cached


def test_streamed_source_scalars_reach_python_input_without_json_failures(monkeypatch, tmp_path):
    identity = UUID("12345678-1234-5678-1234-567812345678")
    rows = [{"id": identity, "time": time(9, 30), "elapsed": timedelta(seconds=90)}]
    monkeypatch.setattr(query, "source_connection", lambda: nullcontext(SourceConnection(rows)))
    columns = [{"name": name} for name in rows[0]]
    count, _, _ = _spool_rows(tmp_path, query.stream_rows("SELECT * FROM source"), columns)
    assert count == 1
    assert json.loads((tmp_path / "rows.jsonl").read_text()) == [[str(identity)], ["09:30:00"], [90.0]]
