from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.raw_sql import compile_raw_sql, form_to_sql
from app.schemas import DatasetSpec


SOURCE = {
    "id": "inline", "schema_name": "demo", "table_name": "inline_measurements",
    "primary_key": "id", "project_column": "project_id",
    "columns": [
        {"name": "id", "type": "bigint"},
        {"name": "project_id", "type": "integer"},
        {"name": "value", "type": "double precision"},
        {"name": "usl", "type": "double precision"},
        {"name": "metric", "type": "text"},
    ],
}


def test_star_calculations_and_source_condition():
    result = compile_raw_sql("""
        SELECT t.*, t.value * 1000 AS value_scaled,
               CASE WHEN t.value > t.usl THEN 'FAIL' ELSE 'PASS' END AS spec_result
        FROM demo.inline_measurements AS t
        WHERE t.project_id = 1 AND (t.metric = 'thickness' OR t.value IS NULL)
    """, SOURCE)
    assert [column["name"] for column in result["columns"]] == ["id", "project_id", "value", "usl", "metric", "value_scaled", "spec_result"]
    assert result["expressions"]["value_scaled"] == '"value" * :e5_0'
    assert result["params"] == {"e5_0": 1000, "e6_0": "PASS", "e6_1": "FAIL", "rawWhere_0": 1, "rawWhere_1": "thickness"}
    assert '"project_id" = :rawWhere_0 AND (' in result["where_sql"]
    assert "__row_id" not in result["expressions"]


def test_rename_cast_and_omitted_pk_do_not_change_hidden_key_contract():
    result = compile_raw_sql("SELECT value AS measurement, CAST(value AS numeric(12, 3)) AS rounded FROM inline_measurements", SOURCE)
    assert result["columns"] == [{"name": "measurement", "type": "double precision"}, {"name": "rounded", "type": "derived"}]
    assert result["where_sql"] == "TRUE"
    assert "id" not in result["expressions"]
    assert "DECIMAL(12, 3)" in result["expressions"]["rounded"]


@pytest.mark.parametrize("sql", [
    "SELECT inline_measurements.value FROM inline_measurements",
    "SELECT demo.inline_measurements.value FROM demo.inline_measurements",
    'SELECT "demo"."inline_measurements"."value" FROM "demo"."inline_measurements"',
    "SELECT T.VALUE FROM DEMO.INLINE_MEASUREMENTS T",
    'SELECT "T"."value" FROM demo.inline_measurements "T"',
])
def test_registered_qualified_columns_and_aliases(sql):
    assert compile_raw_sql(sql, SOURCE)["expressions"] == {"value": '"value"'}


def test_literals_remain_parameters_in_select_and_where():
    result = compile_raw_sql("SELECT 'x; DROP TABLE wafers' AS note FROM inline_measurements WHERE metric = 'O''Brien; -- harmless string'", SOURCE)
    assert result["expressions"]["note"] == ":e0_0"
    assert "DROP" not in result["expressions"]["note"]
    assert result["params"] == {"e0_0": "x; DROP TABLE wafers", "rawWhere_0": "O'Brien; -- harmless string"}


@pytest.mark.parametrize("sql", [
    "DELETE FROM inline_measurements",
    "SELECT * FROM inline_measurements; DROP TABLE wafers",
    "SELECT * INTO copied FROM inline_measurements",
    "SELECT * FROM inline_measurements FOR UPDATE",
    "SELECT * FROM inline_measurements FOR SHARE",
    "SELECT * FROM inline_measurements WHERE true; SELECT 1",
    "WITH x AS (SELECT * FROM inline_measurements) SELECT * FROM x",
    "SELECT value FROM inline_measurements UNION SELECT value FROM other",
    "SELECT DISTINCT value FROM inline_measurements",
    "SELECT value FROM inline_measurements HAVING value > 1",
    "SELECT value FROM inline_measurements ORDER BY id",
    "SELECT value FROM inline_measurements LIMIT 100",
    "SELECT value FROM inline_measurements OFFSET 1",
    "SELECT value FROM inline_measurements JOIN wafers USING(id)",
    "SELECT value FROM inline_measurements, wafers",
    "SELECT (SELECT value FROM inline_measurements) AS nested FROM inline_measurements",
    "SELECT * FROM (SELECT * FROM inline_measurements) x",
    "SELECT value FROM inline_measurements WHERE id IN (SELECT id FROM wafers)",
    "SELECT value FROM inline_measurements TABLESAMPLE BERNOULLI (10)",
    "SELECT value FROM inline_measurements t(id,project_id,value,usl,metric)",
    "SELECT value FROM public.inline_measurements",
    "SELECT value FROM other_database.demo.inline_measurements",
    "SELECT value FROM wafers",
    "SELECT value FROM generate_series(1, 100)",
    "SELECT value",
    "SELECT other.value FROM inline_measurements",
    "SELECT demo.other.value FROM inline_measurements",
    "SELECT inline_measurements.value FROM inline_measurements t",
    "SELECT demo.inline_measurements.value FROM inline_measurements t",
    "SELECT hidden FROM inline_measurements",
    "SELECT __row_id FROM inline_measurements",
    "SELECT value AS __row_id FROM inline_measurements",
    'SELECT value AS "' + 'x' * 64 + '" FROM inline_measurements',
    "SELECT *, value * 2 AS value FROM inline_measurements",
    "SELECT *, * FROM inline_measurements",
    "SELECT value AS result, usl AS result FROM inline_measurements",
    "SELECT value * 2 FROM inline_measurements",
    "SELECT * EXCEPT (value) FROM inline_measurements",
    "SELECT t.* REPLACE (value * 2 AS value) FROM inline_measurements t",
    "SELECT value * 2 AS scaled FROM inline_measurements WHERE scaled > 1",
    "SELECT pg_sleep(10) AS delay FROM inline_measurements",
    "SELECT pg_read_file('/etc/passwd') AS contents FROM inline_measurements",
    "SELECT set_config('search_path','public',true) AS setting FROM inline_measurements",
    "SELECT nextval('sequence') AS next_id FROM inline_measurements",
    "SELECT sum(value) OVER () AS total FROM inline_measurements",
    "SELECT CAST(value AS regclass) AS unsafe FROM inline_measurements",
    "SELECT CAST(value AS numeric(100000,3)) AS huge FROM inline_measurements",
    "SELECT value FROM inline_measurements WHERE pg_sleep(1) IS NULL",
    "SELECT value FROM inline_measurements -- comment",
    "SELECT /* comment */ value FROM inline_measurements",
    "SELECT FROM inline_measurements",
    "",
])
def test_unsafe_or_non_row_preserving_sql_is_rejected(sql):
    with pytest.raises(HTTPException) as error:
        compile_raw_sql(sql, SOURCE)
    assert error.value.status_code == 422


def test_query_and_projection_size_limits():
    with pytest.raises(HTTPException, match="20,000"):
        compile_raw_sql("SELECT " + " " * 20000 + "* FROM inline_measurements", SOURCE)
    wide = deepcopy(SOURCE)
    wide["columns"] = [{"name": f"c{index}", "type": "integer"} for index in range(101)]
    with pytest.raises(HTTPException, match="100"):
        compile_raw_sql("SELECT * FROM inline_measurements", wide)
    with pytest.raises(HTTPException, match="너무 복잡"):
        compile_raw_sql("SELECT " + ",".join(f"value AS c{i}" for i in range(700)) + " FROM inline_measurements", SOURCE)


def test_excessive_parentheses_return_validation_error_instead_of_recursion_error():
    with pytest.raises(HTTPException, match="중첩") as error:
        compile_raw_sql("SELECT " + "(" * 600 + "value" + ")" * 600 + " FROM inline_measurements", SOURCE)
    assert error.value.status_code == 422


def test_export_form_expands_alias_filter_and_retains_project_scope():
    spec = DatasetSpec(source_id="inline", columns=[
        {"name": "id", "expression": "id"},
        {"name": "scaled", "expression": "value * 1000"},
        {"name": "state", "expression": "CASE WHEN value > usl THEN 'FAIL' ELSE 'PASS' END"},
    ], filters=[{"column": "scaled", "op": "gt", "value": 1500}, {"column": "state", "op": "eq", "value": "FAIL"}])
    sql = form_to_sql(spec, SOURCE, 2)
    compiled = compile_raw_sql(sql, SOURCE)
    assert 's.id AS id' not in sql
    assert 's.id,' in sql
    assert 'AS scaled' in sql and 'AS state' in sql
    assert 'FROM demo.inline_measurements' in sql
    assert 's.project_id = 2' in sql
    assert '"scaled"' not in compiled["where_sql"]
    assert '"state"' not in compiled["where_sql"]
    assert '"value" *' in compiled["where_sql"]
    assert "CASE WHEN" in compiled["where_sql"]
    assert {value for key, value in compiled["params"].items() if key.startswith("rawWhere")} == {1000, 1500, "PASS", "FAIL", 2}


def test_export_filters_round_trip_quotes_wildcards_nulls_and_booleans():
    spec = DatasetSpec(source_id="inline", filters=[
        {"column": "metric", "op": "contains", "value": "O'Brien_100%\\"},
        {"column": "value", "op": "ne", "value": None},
        {"column": "project_id", "op": "in", "value": [1, 2]},
        {"column": "metric", "op": "eq", "value": "A'; DELETE FROM x; --"},
    ])
    sql = form_to_sql(spec, SOURCE)
    compiled = compile_raw_sql(sql, SOURCE)
    assert "NOT" in compiled["where_sql"] and "IS NULL" in compiled["where_sql"]
    assert "%O'Brien\\_100\\%\\\\%" in compiled["params"].values()
    assert "A'; DELETE FROM x; --" in compiled["params"].values()
    assert "DELETE" not in compiled["where_sql"]


@pytest.mark.parametrize("operator", ["eq", "ne", "gt", "gte", "lt", "lte"])
def test_export_comparison_operators(operator):
    spec = DatasetSpec(source_id="inline", filters=[{"column": "value", "op": operator, "value": 4}])
    compiled = compile_raw_sql(form_to_sql(spec, SOURCE), SOURCE)
    assert compiled["params"] == {"rawWhere_0": 4}


def test_export_empty_in_and_null_eq():
    spec = DatasetSpec(source_id="inline", filters=[{"column": "id", "op": "in", "value": []}, {"column": "value", "op": "eq", "value": None}])
    compiled = compile_raw_sql(form_to_sql(spec, SOURCE), SOURCE)
    assert "FALSE" in compiled["where_sql"]
    assert '"value" IS NULL' in compiled["where_sql"]
    assert not compiled["params"]


def test_export_shared_equipment_ignores_project_scope():
    source = deepcopy(SOURCE)
    source["project_column"] = None
    sql = form_to_sql(DatasetSpec(source_id="inline"), source, 2)
    assert "WHERE" not in sql


def test_export_rejects_registered_join_and_unknown_filters():
    with pytest.raises(HTTPException, match="관계"):
        form_to_sql(DatasetSpec(source_id="inline", joins=["equipment"]), SOURCE)
    with pytest.raises(HTTPException, match="필터 컬럼"):
        form_to_sql(DatasetSpec(source_id="inline", filters=[{"column": "missing", "op": "eq", "value": 1}]), SOURCE)


def test_export_raw_text_is_normalized_to_same_form_and_sql():
    class RawSpec:
        raw_sql = "select value from inline_measurements where value > 1"
    sql = form_to_sql(RawSpec(), SOURCE, 2)
    form = compile_raw_sql(sql,SOURCE)["spec"]
    assert form.raw_sql is None
    assert form_to_sql(form,SOURCE,2) == sql
    assert 's.project_id = 2' in sql
    assert '"s"."value" AS "value"' not in sql
    assert [c.name for c in form.columns] == ["value"]
