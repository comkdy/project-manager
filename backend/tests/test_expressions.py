from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.expressions import compile_expression, quote


@pytest.mark.parametrize("sql", [
    "SELECT value FROM measurements", "(SELECT value FROM measurements)",
    "value; DROP TABLE wafers", "pg_sleep(10)", "set_config('search_path','public',true)",
    "current_setting('server_version')", "pg_read_file('/etc/passwd')",
    "sum(value) OVER ()", "sum(value)", "value + hidden_column",
    "public.value + 1", "value -- comment", "CAST(value AS regclass)",
    "CAST(value AS public.custom_type)", "ARRAY[value]", "value AS hidden",
    "(WITH x AS (DELETE FROM wafers RETURNING id) SELECT id FROM x)",
    "CAST(value AS numeric(100000, 3))",
])
def test_forbidden_expression(sql):
    with pytest.raises(HTTPException) as exc:
        compile_expression(sql, {"value", "lsl", "usl"})
    assert exc.value.status_code == 422


def test_literals_are_bound_even_if_look_like_sql():
    sql,params = compile_expression("CASE WHEN value > usl THEN 'x; DROP TABLE a' ELSE 'PASS' END", {"value","usl"})
    assert "DROP" not in sql
    assert '"value" > "usl"' in sql
    assert "x; DROP TABLE a" in params.values()


def test_calculation_casts_and_nulls():
    sql,params = compile_expression("ROUND(CAST(COALESCE(value, 0) * 10 AS NUMERIC(12, 3)), 2)", {"value"})
    assert '"value"' in sql
    assert "DECIMAL(12, 3)" in sql
    assert sorted(params.values()) == [Decimal(0),Decimal(2),Decimal(10)]


def test_date_trunc_supported():
    sql,params = compile_expression("date_trunc('month', measured_at)", {"measured_at"})
    assert "DATE_TRUNC('MONTH'" in sql.upper()


@pytest.mark.parametrize("name", ["a;b", "a.b", '"quote"', "a b", "a--b", ""])
def test_identifier_guard(name):
    with pytest.raises(HTTPException):
        quote(name)


def test_explicit_joined_column():
    sql,params = compile_expression("equipment__id + 1", {"equipment__id"})
    assert '"equipment__id"' in sql
@pytest.mark.parametrize("expression,value", [("'a quoted '' value'", "a quoted ' value"), ("123", 123)])
def test_root_literal_is_bound(expression, value):
    sql, params = compile_expression(expression, set())
    assert sql == ":expr_0"
    assert params == {"expr_0": value}
