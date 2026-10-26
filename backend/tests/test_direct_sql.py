import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import psycopg

from app.direct_sql import embedded_query, parse_query, referenced_sources, sqlalchemy_literal
from app.schemas import DatasetSpec
from app.simple_editor import prepare_simple_spec, simple_editor_state
from app.raw_sql import form_to_sql

SOURCE = {"id": "facts", "schema_name": "public", "table_name": "facts", "primary_key": "id",
          "project_column": "project_id", "columns": [{"name": n, "type": "integer"}
          for n in ("id", "project_id", "value")]}


@pytest.mark.parametrize("sql", [
    "DELETE FROM facts", "SELECT 1; SELECT 2", "SELECT * INTO copied FROM facts",
    "WITH changed AS (DELETE FROM facts RETURNING *) SELECT * FROM changed",
    "SELECT * FROM facts FOR UPDATE", "SELECT * FROM facts FOR SHARE",
    "CREATE TABLE copied (id int)", "TRUNCATE facts", "DROP TABLE facts",
    "UPDATE facts SET value=1", "INSERT INTO facts SELECT * FROM facts",
    "SELECT 1; /* another statement */ DELETE FROM facts",
    "WITH changed AS (UPDATE facts SET value=1 RETURNING *) SELECT * FROM changed",
    "SELECT set_config('transaction_read_only','off',true)",
    "SELECT pg_catalog.set_config('statement_timeout','0',false)",
    'SELECT "set_config"(\'transaction_read_only\',\'off\',true)',
    "SELECT nextval('facts_id_seq')", "SELECT setval('facts_id_seq',1)",
    "SELECT pg_notify('channel','message')", "SELECT pg_advisory_lock(1)",
    "SELECT lo_import('/tmp/input')", "SELECT pg_read_file('/etc/passwd')",
    "SELECT dblink_exec('connection','DELETE FROM facts')",
    "SELECT query_to_xml('DELETE FROM facts RETURNING *',true,true,'')",
    "SELECT public.custom_function()", "SELECT public.lower('test')",
])
def test_only_read_queries(sql):
    with pytest.raises(HTTPException):
        parse_query(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM (SELECT * FROM public.facts) f",
    "WITH f AS (SELECT * FROM facts) SELECT * FROM f",
    "SELECT id FROM facts UNION ALL SELECT id FROM facts",
    "SELECT ROW_NUMBER() OVER (ORDER BY id) AS n FROM facts",
    "SELECT a.id FROM facts a JOIN facts b ON a.id=b.id",
])
def test_complex_reads_resolve_physical_sources(sql):
    assert referenced_sources(parse_query(sql), [SOURCE]) == [SOURCE]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM secret", "WITH f AS (SELECT * FROM secret) SELECT * FROM f",
    "SELECT (SELECT COUNT(*) FROM secret) FROM facts",
])
def test_unregistered_tables_rejected(sql):
    with pytest.raises(HTTPException):
        referenced_sources(parse_query(sql), [SOURCE])


@pytest.mark.parametrize("suffix", [
    "HAVING COUNT(*)>1", "ORDER BY id", "LIMIT 1", "OFFSET 2",
    "JOIN facts b ON a.id=b.id",
])
def test_simple_editor_rejects_unrenderable_clauses(suffix):
    assert not simple_editor_state("SELECT a.id FROM facts a " + suffix, SOURCE)["available"]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM (SELECT * FROM facts) f",
    "WITH f AS (SELECT * FROM facts) SELECT * FROM f",
    "SELECT ROW_NUMBER() OVER () AS n FROM facts",
    "SELECT DISTINCT id FROM facts", "SELECT id FROM facts WHERE (",
    "SELECT id FROM facts UNION SELECT id FROM facts",
])
def test_simple_editor_rejects_complex_or_invalid_sql(sql):
    assert not simple_editor_state(sql, SOURCE)["available"]


def test_simple_filters_stay_where_with_aggregation_and_keyword_literals():
    spec = DatasetSpec(source_id="facts", columns=[
        {"name": "project_id", "expression": "project_id"}, {"name": "mean", "expression": "AVG(value)"},
    ], group_by=["project_id"], filters=[{"column": "value", "op": "gt", "value": 10}])
    sql = form_to_sql(prepare_simple_spec(spec, SOURCE), SOURCE)
    assert "WHERE" in sql and "HAVING" not in sql
    state = simple_editor_state(sql, SOURCE)
    assert state["available"]
    assert state["spec"]["filters"] == [{"column": "value", "op": "gt", "value": 10}]
    assert simple_editor_state("SELECT 'join having with' AS label FROM facts", SOURCE)["available"]
    assert simple_editor_state("SELECT id FROM facts; -- trailing comment", SOURCE)["available"]
    assert simple_editor_state("-- keep this SQL\nSELECT id /* source key */ FROM facts", SOURCE)["available"]


def test_embedding_preserves_comments_colons_and_percent():
    sql = "SELECT ':value', '\\:value', '10%', 1::int FROM facts; -- last comment"
    embedded = embedded_query(sql)
    assert embedded == sql.replace("; --", " --") + "\n"
    compiled = text(sqlalchemy_literal(embedded)).compile(dialect=psycopg.dialect())
    assert not compiled.params
    assert str(compiled).replace("%%", "%") == embedded
