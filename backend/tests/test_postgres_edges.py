"""Edge cases exercised only against the isolated PostgreSQL demo databases."""
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, settings
from app.models import DatasetVersion, Source
from app.query import execute_rows


@pytest.fixture(scope="module")
def api():
    if not settings.demo_mode or not settings.app_database_url:
        pytest.skip("Requires isolated demo PostgreSQL")
    from app.main import app
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(settings,"auth_mode","local_admin")
        with TestClient(app,client=("127.0.0.1",50003)) as client:
            result=client.get("/api/auth/session")
            assert result.json()["status"]=="authenticated",result.text
            yield client


def test_preview_aligns_original_rows_after_derived_filter_and_sort(api):
    spec={"source_id":"inline_measurements","columns":[{"name":"id","expression":"id"},{"name":"scaled","expression":"value * 10"}],
          "filters":[{"column":"scaled","op":"gt","value":950}]}
    result=api.post("/api/datasets/preview",json={"spec":spec,"context":{"project_id":1,"limit":7,"sort":"scaled","direction":"desc"}})
    assert result.status_code==200,result.text
    data=result.json()
    assert len(data["rows"])==7
    assert [row["id"] for row in data["rows"]]==[row["id"] for row in data["before_rows"]]
    for before,after in zip(data["before_rows"],data["rows"]):
        assert after["scaled"]==pytest.approx(before["value"]*10)
        assert before["project_id"]==1
    assert {col["name"] for col in data["columns"]}=={"id","scaled"}


@pytest.mark.parametrize("direction",["asc","desc"])
def test_keyset_walk_with_null_sort_has_no_duplicate_or_missing_rows(api,direction):
    first_ids=execute_rows("SELECT id FROM public.inline_measurements ORDER BY id LIMIT 55")
    last_id=first_ids[-1]["id"]
    spec={"source_id":"inline_measurements","columns":[{"name":"id","expression":"id"},{"name":"v","expression":"CASE WHEN id % 3 = 0 THEN NULL ELSE value END"}],
          "filters":[{"column":"id","op":"lte","value":last_id}]}
    created=api.post("/api/datasets",json={"name":"NULL cursor QA "+uuid.uuid4().hex[:6],"spec":spec})
    assert created.status_code==201,created.text
    dataset=created.json()
    try:
        rows=[]
        context={"limit":7,"sort":"v","direction":direction}
        for _ in range(20):
            result=api.post("/api/query",json={"dataset_id":dataset["id"],"dataset_version":1,"context":context})
            assert result.status_code==200,result.text
            data=result.json()
            rows+=data["rows"]
            if not data["next_cursor"]:
                break
            context["cursor"]=data["next_cursor"]
        assert len(rows)==55
        assert len({row["id"] for row in rows})==55
        values=[row["v"] for row in rows if row["v"] is not None]
        assert values==sorted(values,reverse=direction=="desc")
        first_null=next(i for i,row in enumerate(rows) if row["v"] is None)
        assert all(row["v"] is None for row in rows[first_null:])
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==dataset["id"]))


def test_registered_join_keeps_row_grain_and_scope(api):
    result=api.post("/api/datasets/preview",json={"spec":{"source_id":"inline_measurements","joins":["inline_measurements_equipment"],
             "columns":[{"name":"id","expression":"id"},{"name":"project_id","expression":"project_id"},{"name":"equipment_name","expression":"equipment__name"}]},"context":{"project_id":2,"limit":15}})
    assert result.status_code==200,result.text
    rows=result.json()["rows"]
    assert len(rows)==15
    assert len({row["id"] for row in rows})==15
    assert all(row["project_id"]==2 and row["equipment_name"] for row in rows)


def test_original_schema_drift_returns_actionable_error(api):
    id="qa_drift_"+uuid.uuid4().hex[:10]
    with SessionLocal.begin() as db:
        original=db.get(Source,"inline_measurements")
        columns=original.columns+[{"name":"removed_column","type":"double precision"}]
        db.add(Source(id=id,name="Schema drift QA",schema_name="public",table_name="inline_measurements",project_column="project_id",primary_key="id",columns=columns))
        db.add(DatasetVersion(id=id,name="Schema drift QA",version=1,spec={"source_id":id,"columns":[],"filters":[],"joins":[]},columns=columns))
    try:
        result=api.post("/api/query",json={"dataset_id":id,"dataset_version":1,"context":{"limit":1}})
        assert result.status_code==409,result.text
        assert "스키마" in result.json()["detail"]
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==id))
            db.execute(delete(Source).where(Source.id==id))


def test_timeout_is_transaction_local_and_connection_recovers(api,monkeypatch):
    with monkeypatch.context() as patched:
        patched.setattr(settings,"query_timeout_ms",1)
        with pytest.raises(HTTPException) as error:
            execute_rows("SELECT pg_sleep(0.05)")
        assert error.value.status_code==408
    assert execute_rows("SELECT 1 AS n")==[{"n":1}]


def test_empty_derived_expression_has_inferred_types(api):
    result=api.post("/api/datasets/preview",json={"spec":{"source_id":"inline_measurements",
        "columns":[{"name":"scaled","expression":"CAST(value AS numeric) * 10"}],
        "filters":[{"column":"scaled","op":"gt","value":1000000000}]}})
    assert result.status_code==200,result.text
    assert result.json()["rows"]==[]
    assert result.json()["before_rows"]==[]
    assert result.json()["columns"]==[{"name":"scaled","type":"numeric"}]


def test_csrf_origin_rejected(api):
    result=api.post("/api/auth/logout",headers={"Origin":"https://untrusted.example"})
    assert result.status_code==403
    assert api.get("/api/auth/me").status_code==200


def test_histogram_is_a_server_aggregate(api):
    base={"id":"aggregate-qa","title":"SQL aggregate QA","dataset_id":"inline_measurements","dataset_version":1,
          "renderer":"plotly","filters":[{"column":"metric_name","op":"eq","value":"thickness"},{"column":"process_step","op":"eq","value":"DEP"}],"options":{"bins":15}}
    histogram=api.post("/api/cards/query",json={"card":dict(base,kind="histogram",x="value",aggregate="count"),"context":{"project_id":1,"refresh":True}})
    assert histogram.status_code==200,histogram.text
    count=execute_rows("SELECT count(value) AS n FROM public.inline_measurements WHERE project_id=1 AND metric_name='thickness' AND process_step='DEP'")[0]["n"]
    assert sum(row["y"] for row in histogram.json()["rows"])==count
    assert len(histogram.json()["rows"])<=15
