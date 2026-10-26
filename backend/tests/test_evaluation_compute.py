"""Real PostgreSQL aggregate SQL, with synthetic VALUES only and no source writes."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app import db as database
from app import evaluation_compute as calculator
from app.evaluation_analysis import Settings


@pytest.fixture
def source():
    if database.source_engine is None:
        pytest.skip("An explicit read-only PostgreSQL fixture is required")
    binding = database.SourceBinding(database.source_engine, database.database_key(database.settings.source_database_url))
    with database.use_source(binding):
        yield


def stored(settings, sources=None):
    return {"_evaluation_dataset": {"id": "synthetic", "name": "합성 비교", "version": 1, "sources": sources or []},
            "_evaluation_scope": {"filters": [], "period": None}, "settings": settings.model_dump(mode="json")}


def calculate(settings, rows, columns=None, sources=None):
    columns = columns or [{"name": "recipe", "type": "text"}, {"name": "alpha", "type": "double precision"},
                          {"name": "beta", "type": "double precision"}]
    names = ",".join('"' + c["name"] + '"' for c in columns)
    cte = f"WITH filtered AS (SELECT * FROM (VALUES {rows}) AS source({names}))"
    return calculator.compute(cte, {}, settings, columns, stored(settings, sources))


def test_real_group_statistics_deltas_and_nonfinite_exclusion(source):
    settings = Settings(method="group_compare", group_column="recipe", metrics=["alpha", "beta"], baseline="R1", comparison="R2")
    result = calculate(settings, "('R1',1::float8,10::float8),('R1',3,30),('R2',5,50),('R2',7,70),('R2','NaN'::float8,NULL)")
    a, b = result["groups"]
    assert a["metrics"][0]["mean"] == 2 and b["metrics"][0]["mean"] == 6
    assert a["metrics"][0]["stddev"] == pytest.approx(2 ** .5)
    assert b["metrics"][0]["missing_count"] == 1
    assert result["deltas"][0]["delta"] == 4 and result["deltas"][0]["percent_change"] == 200
    assert result["row_count"] == 5 and result["knowledge_eligible"]
    assert "유효 2행" in result["summary"] and "확정하지 않습니다" in result["summary"]


def test_real_same_row_correlation_and_degenerate_input(source):
    settings = Settings(method="correlation", metrics=["alpha", "beta"])
    result = calculate(settings, "('R1',1::float8,9::float8),('R1',2,6),('R2',3,3),('R2',NULL,10),('R2',4,'Infinity'::float8)")
    assert result["correlation"] == {"x": "alpha", "y": "beta", "pair_count": 3, "excluded_count": 2, "pearson": -1}
    flat = calculate(settings, "('R1',1::float8,9::float8),('R1',1,8)")
    assert flat["correlation"]["pearson"] is None and not flat["knowledge_eligible"]


def test_real_missing_group_and_bounds_fail_closed(source, monkeypatch):
    settings = Settings(method="group_compare", group_column="recipe", metrics=["alpha"], baseline="R0", comparison="R2")
    with pytest.raises(HTTPException) as missing:
        calculate(settings, "('R1',1::float8,2::float8),('R2',3,4)")
    assert "현재 범위에 없습니다" in missing.value.detail
    settings = Settings(method="group_compare", group_column="recipe", metrics=["alpha"])
    monkeypatch.setattr(calculator, "MAX_ROWS", 1)
    with pytest.raises(HTTPException) as count:
        calculate(settings, "('R1',1::float8,2::float8),('R2',3,4)")
    assert "일부 행만" in count.value.detail
    monkeypatch.setattr(calculator, "MAX_ROWS", 10)
    monkeypatch.setattr(calculator, "MAX_GROUPS", 1)
    with pytest.raises(HTTPException) as groups:
        calculate(settings, "('R1',1::float8,2::float8),('R2',3,4)")
    assert "비교 그룹" in groups.value.detail


def test_long_form_mixed_series_and_hidden_identity_are_blocked(source):
    settings = Settings(method="group_compare", group_column="recipe", metrics=["value"])
    columns = [{"name": "recipe", "type": "text"}, {"name": "parameter", "type": "text"}, {"name": "value", "type": "double precision"}]
    with pytest.raises(HTTPException) as mixed:
        calculate(settings, "('R1','PARA1',1::float8),('R1','PARA2',2::float8)", columns)
    assert "한 공정·PARA" in mixed.value.detail
    result = calculate(settings, "('R1','PARA1',1::float8),('R2','PARA1',2::float8)", columns)
    assert len(result["groups"]) == 2
    with pytest.raises(HTTPException) as hidden:
        calculate(settings, "('R1',1::float8),('R2',2::float8)", [columns[0], columns[2]],
                  sources=[{"columns": {"parameter": "text", "value": "double precision"}}])
    assert "PARA 구분이 빠져" in hidden.value.detail


def test_all_null_groups_never_become_knowledge(source):
    result = calculate(Settings(method="group_compare", group_column="recipe", metrics=["alpha"]),
                       "('R1',NULL::float8,1::float8),('R2',NULL::float8,2::float8)")
    assert not result["knowledge_eligible"]
    assert all(group["metrics"][0]["mean"] is None for group in result["groups"])
