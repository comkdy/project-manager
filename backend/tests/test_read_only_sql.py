import pytest
from fastapi import HTTPException
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.db import source_engine
from app.query import execute_rows, source_connection
from app.source_policy import require_read_only_role
from test_direct_sql_api import api, preview


@pytest.mark.parametrize("sql", [
    "CREATE TABLE should_not_exist (id int)", "DELETE FROM inline_measurements",
    "UPDATE inline_measurements SET value=0", "DROP TABLE inline_measurements",
    "SELECT 1; DELETE FROM inline_measurements",
    "WITH changed AS (DELETE FROM inline_measurements RETURNING *) SELECT * FROM changed",
    "SELECT set_config('transaction_read_only','off',true)",
    "SELECT pg_catalog.pg_notify('channel','payload')",
    "SELECT * FROM pg_catalog.pg_authid",
    "SELECT id FROM inline_measurements FOR UPDATE",
])
def test_attack_queries_never_reach_source_database(api, sql):
    sent = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        sent.append(statement)
    event.listen(source_engine, "before_cursor_execute", capture)
    try:
        response = preview(api, sql)
    finally:
        event.remove(source_engine, "before_cursor_execute", capture)
    assert response.status_code == 422, response.text
    assert not sent


@pytest.mark.parametrize("sql", [
    "CREATE TEMP TABLE qa_read_only_probe (id integer)",
    "DELETE FROM public.inline_measurements WHERE FALSE",
    "UPDATE public.inline_measurements SET value=value WHERE FALSE",
    "INSERT INTO public.inline_measurements SELECT * FROM public.inline_measurements WHERE FALSE",
])
def test_database_itself_rejects_writes_even_without_sql_parser(api, sql):
    # No real rows are changed even if this assertion ever detects a regression;
    # every probe is enclosed in a rolled-back transaction.
    with source_engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert connection.scalar(text("SHOW transaction_read_only")) == "on"
            with pytest.raises(DBAPIError) as error:
                connection.exec_driver_sql(sql)
            assert error.value.orig.sqlstate == "25006"
        finally:
            transaction.rollback()
    assert execute_rows("SELECT 1 AS healthy") == [{"healthy": 1}]


def test_runtime_enforces_read_only_when_role_default_is_overridden(api):
    with source_engine.connect() as connection:
        connection.exec_driver_sql("SET default_transaction_read_only=off")
        connection.commit()
    try:
        with source_connection() as connection:
            assert connection.scalar(text("SHOW transaction_read_only")) == "on"
            require_read_only_role(connection)
    finally:
        source_engine.dispose()


def test_misconfigured_write_role_fails_closed():
    class UnsafeConnection:
        def scalar(self, query):
            return True
    with pytest.raises(HTTPException) as error:
        require_read_only_role(UnsafeConnection())
    assert error.value.status_code == 503


def test_injection_text_in_filter_stays_a_literal(api):
    value = "x' OR 1=1; DELETE FROM inline_measurements; --"
    response = api.post("/api/datasets/preview", json={
        "editor_mode": "simple",
        "spec": {"source_id": "inline_measurements",
                 "columns": [{"name": "id", "expression": "id"}],
                 "filters": [{"column": "metric_name", "op": "eq", "value": value}]},
    })
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == []
    assert "x'' OR 1=1" in response.json()["canonical_sql"]
