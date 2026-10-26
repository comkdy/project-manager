import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.query import _dataset_cte, _decode_cursor, _encode_cursor, _filter_sql, validate_card
from app.schemas import CardOptions, CardSpec, DatasetSpec, Filter, QueryContext


def test_global_missing_filter_is_reported_but_card_filter_is_strict():
    params,ignored = {},[]
    assert _filter_sql([Filter(column="lot_code",op="eq",value="LOT")],set(),params,"q",ignored)=="TRUE"
    assert ignored == ["lot_code"]
    with pytest.raises(HTTPException):
        _filter_sql([Filter(column="lot_code",op="eq",value="LOT")],set(),{},"c")


def test_filter_values_are_never_sql_text():
    malicious = "'; DROP TABLE wafers;--"
    params = {}
    sql = _filter_sql([Filter(column="lot_code",op="eq",value=malicious)],{"lot_code"},params,"q")
    assert malicious not in sql
    assert params["q_0"] == malicious


def test_empty_in_and_null_filter():
    assert _filter_sql([Filter(column="value",op="in",value=[])],{"value"},{},"q") == "FALSE"
    assert _filter_sql([Filter(column="value",op="not_in",value=[])],{"value"},{},"q") == "TRUE"
    assert _filter_sql([Filter(column="value",op="ne",value=None)],{"value"},{},"q") == '"value" IS NOT NULL'


@pytest.mark.parametrize('op,operator', [('in', 'IN'), ('not_in', 'NOT IN')])
def test_in_values_are_bound_individually_including_sql_like_text(op, operator):
    values = ['A,B', "x'); DROP TABLE wafers;--", '100%_']
    params = {}
    sql = _filter_sql([Filter(column='name', op=op, value=values)], {'name'}, params, 'q')
    assert sql == f'"name" {operator} (:q_0_0, :q_0_1, :q_0_2)'
    assert list(params.values()) == values


@pytest.mark.parametrize('value', ['1,3', list(range(101)), [[1]], {'id': 1}])
def test_not_in_rejects_invalid_lists(value):
    with pytest.raises(HTTPException) as error:
        _filter_sql([Filter(column='id', op='not_in', value=value)], {'id'}, {}, 'q')
    assert error.value.status_code == 422


def test_contains_escapes_wildcards():
    params={}
    _filter_sql([Filter(column="name",op="contains",value="a_b%")],{"name"},params,"q")
    assert params["q_0"]=="%a\\_b\\%%"


def test_scope_applied_before_join_and_project_keys_compared():
    spec=DatasetSpec(source_id="facts")
    compiled={"source":{"id":"facts","schema_name":"public","table_name":"facts","project_column":"project_id","primary_key":"id","columns":[{"name":"id","type":"bigint"},{"name":"project_id","type":"integer"},{"name":"wafer_id","type":"bigint"}]},
              "spec":spec,"params":{},"expressions":{},
              "columns":[{"name":"id","type":"bigint"}],
              "relations":[({"left_column":"wafer_id","right_column":"id","join_type":"left"},
                            {"id":"wafers","schema_name":"public","table_name":"wafers","project_column":"project_id","columns":[{"name":"id","type":"bigint"},{"name":"project_id","type":"integer"}]})]}
    compiled["base_columns"] = compiled["source"]["columns"]
    sql,params,ignored=_dataset_cte(compiled,QueryContext(project_id=2))
    assert '(SELECT * FROM "public"."facts" WHERE "project_id" = :project_id) s' in sql
    assert '(SELECT * FROM "public"."wafers" WHERE "project_id" = :project_id) j0' in sql
    assert 's."project_id" = j0."project_id"' in sql
    assert params["project_id"]==2


def test_cursor_cannot_be_reused_for_other_query():
    cursor=_encode_cursor(10, "2026-10-01", "context-A")
    assert _decode_cursor(cursor,"context-A")["id"]==10
    with pytest.raises(HTTPException):
        _decode_cursor(cursor,"context-B")


@pytest.mark.parametrize("cursor", ["not-base64",base64.urlsafe_b64encode(b"[]").decode(),base64.urlsafe_b64encode(b'{}').decode()])
def test_malformed_cursor(cursor):
    with pytest.raises(HTTPException):
        _decode_cursor(cursor,"x")


def test_duplicate_output_columns_rejected():
    with pytest.raises(ValidationError):
        DatasetSpec(source_id="a",columns=[{"name":"x","expression":"id"},{"name":"x","expression":"id"}])


def test_reserved_hidden_row_id_cannot_be_overwritten():
    with pytest.raises(ValidationError):
        DatasetSpec(source_id="a",columns=[{"name":"__row_id","expression":"id"}])


def test_response_limits_and_bin_limits():
    with pytest.raises(ValidationError):
        QueryContext(limit=10000000)
    with pytest.raises(ValidationError):
        CardOptions(bins=10000000)


def test_yield_card_rejects_non_numeric_columns_in_new_dataset_version(monkeypatch):
    columns=[{"name":"tested_dies","type":"bigint"},{"name":"passed_dies","type":"bigint"}]
    dataset=SimpleNamespace(columns=columns,spec={},project_id=None)
    monkeypatch.setattr("app.query.load_dataset",lambda dataset_id,version: dataset)
    monkeypatch.setattr("app.query.validate_dataset",lambda spec: {})
    card=CardSpec(id="yield-qa",title="수율",dataset_id="versioned",dataset_version=2,kind="kpi",aggregate="yield")
    validate_card(card)
    for column in columns:
        column["type"]="text"
        with pytest.raises(HTTPException) as error:
            validate_card(card)
        assert error.value.status_code==422
        assert column["name"] in error.value.detail
        column["type"]="bigint"
