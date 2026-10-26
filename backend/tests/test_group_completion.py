import pytest
from fastapi import HTTPException

from app.group_completion import complete_groups
from app.raw_sql import compile_form
from app.schemas import DatasetSpec


NAMES = ["lot_id", "value", "measured_at", "metric_name", "equipment__name"]
SOURCE = {"id": "facts", "columns": [{"name": name, "type": "integer"} for name in NAMES]}


@pytest.mark.parametrize(("expressions", "expected"), [
    (["lot_id", "AVG(value)"], ['"lot_id"']),
    (["lot_id", "value * 2"], []),
    (["COUNT(*)", "AVG(value)", "'fixed'", "42"], []),
    (["lot_id", '"lot_id"', "COUNT(*)"], ['"lot_id"']),
    (["equipment__name", "SUM(value)"], ['"equipment__name"']),
    (["DATE_TRUNC('day', measured_at)", "AVG(value)"], ["DATE_TRUNC('DAY', \"measured_at\")"]),
    (["lot_id + AVG(value)", "COUNT(*)"], ['"lot_id"']),
    (["ROUND(AVG(value), 2)", "lot_id"], ['"lot_id"']),
    (["COUNT(DISTINCT lot_id)", "SUM(value) FILTER (WHERE value > 0)"], []),
    (["'AVG(value)'", "lot_id"], []),
    (["(lot_id)", "lot_id", "COUNT(*)"], ['"lot_id"']),
])
def test_groups_are_complete_and_compile(expressions, expected):
    result = complete_groups(expressions, NAMES)
    assert result == {"complete": True, "group_by": expected}
    spec = DatasetSpec(source_id="facts", columns=[
        {"name": f"c{i}", "expression": expression} for i, expression in enumerate(expressions)
    ], group_by=result["group_by"])
    compile_form(spec, SOURCE)


def test_having_only_adds_uncovered_nonaggregate_references():
    expressions = ["DATE_TRUNC('day', measured_at)", "COUNT(*)"]
    having = "DATE_TRUNC('day', measured_at) IS NOT NULL AND lot_id > 0 AND AVG(value) > 1"
    result = complete_groups(expressions, NAMES, having)
    assert result["group_by"] == ["DATE_TRUNC('DAY', \"measured_at\")", '"lot_id"']
    compile_form(DatasetSpec(source_id="facts", columns=[
        {"name": f"c{i}", "expression": expression} for i, expression in enumerate(expressions)
    ], group_by=result["group_by"], having_sql=having), SOURCE)


@pytest.mark.parametrize("expression", ["", "AVG(", "SUM(AVG(value))", "unknown + 1", "SELECT value", "value; DROP TABLE facts"])
def test_incomplete_or_unsupported_expressions_never_replace_groups(expression):
    assert complete_groups(["lot_id", expression], NAMES) == {"complete": False, "group_by": []}


def test_group_limit_is_not_silently_truncated():
    names = [f"c{i}" for i in range(31)]
    with pytest.raises(HTTPException):
        complete_groups(names + ["COUNT(*)"], names)
