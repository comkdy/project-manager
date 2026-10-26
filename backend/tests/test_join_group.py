"""Join/group execution and lossless SQL/form round-trips against real PostgreSQL."""
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.db import SessionLocal, settings, source_engine
from app.models import DatasetVersion
from app.raw_sql import compile_raw_sql, form_to_sql
from app.schemas import DatasetSpec

SOURCE={"id":"facts","schema_name":"public","table_name":"facts","primary_key":"id","project_column":"project_id",
        "columns":[{"name":n,"type":"integer"} for n in ("id","project_id","equipment_id","value")]}
TARGET={"id":"equipment","schema_name":"public","table_name":"equipment","primary_key":"id","project_column":None,
        "columns":[{"name":"id","type":"integer"},{"name":"name","type":"text"}]}
RELATIONS=[({"id":"facts_equipment","left_column":"equipment_id","right_column":"id","join_type":"left"},TARGET)]


def test_join_group_form_sql_roundtrip():
    sql="""SELECT e.name AS equipment, COUNT(*) AS n, AVG(f.value) AS mean
           FROM public.facts f LEFT JOIN public.equipment e ON f.equipment_id=e.id
           WHERE f.value IS NOT NULL GROUP BY e.name HAVING COUNT(*) >= 2"""
    compiled=compile_raw_sql(sql,SOURCE,RELATIONS)
    form=compiled["spec"]
    assert form.joins == ["facts_equipment"]
    assert form.group_by == ['"equipment__name"']
    assert form.columns[1].expression == 'COUNT(*)'
    canonical=form_to_sql(form,SOURCE,1,RELATIONS)
    roundtrip=compile_raw_sql(canonical,SOURCE,RELATIONS)["spec"]
    assert form_to_sql(roundtrip,SOURCE,1,RELATIONS)==canonical
    assert roundtrip.group_by==form.group_by


@pytest.mark.parametrize("sql",[
    "SELECT f.id FROM facts f JOIN equipment e ON f.equipment_id=e.id OR TRUE",
    "SELECT f.id FROM facts f JOIN equipment e ON f.id=e.id",
    "SELECT f.id FROM facts f CROSS JOIN equipment e",
    "SELECT f.id FROM facts f RIGHT JOIN equipment e ON f.equipment_id=e.id",
    "SELECT f.id FROM facts f JOIN other e ON f.equipment_id=e.id",
    "SELECT id FROM facts f JOIN equipment e ON f.equipment_id=e.id",
    "SELECT f.id, SUM(f.value) AS total FROM facts f",
    "SELECT SUM(AVG(value)) AS total FROM facts",
    "SELECT value FROM facts GROUP BY SUM(value)",
    "SELECT SUM(value) AS total FROM facts WHERE COUNT(*)>0",
    "SELECT COUNT(*) FILTER (WHERE SUM(value)>1) AS n FROM facts",
    "SELECT value FROM facts GROUP BY ROLLUP(value)",
    "SELECT COUNT(DISTINCT id, value) AS n FROM facts",
    "SELECT value FROM facts GROUP BY " + ", ".join(["value"]*31),
])
def test_join_and_aggregate_rejections(sql):
    with pytest.raises(HTTPException) as error:
        compile_raw_sql(sql,SOURCE,RELATIONS)
    assert error.value.status_code==422


@pytest.fixture(scope="module")
def api():
    if not settings.demo_mode or not settings.app_database_url:
        pytest.skip("Requires isolated demo PostgreSQL")
    from app.main import app
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(settings,"auth_mode","local_admin")
        with TestClient(app,client=("127.0.0.1",50008)) as client:
            yield client


SQL="""SELECT m.lot_code, e.name AS equipment_name, COUNT(*) AS n,
       AVG(m.value) AS mean_value, COUNT(DISTINCT m.wafer_id) AS wafer_count
FROM public.inline_measurements m
LEFT JOIN public.equipment e ON m.equipment_id=e.id
WHERE m.metric_name='thickness' AND m.project_id=1
GROUP BY m.lot_code, e.name HAVING COUNT(*) >= 2"""


def preview(api,sql=SQL,context=None):
    result=api.post("/api/datasets/preview",json={"spec":{"source_id":"inline_measurements","raw_sql":sql},"context":context or {"project_id":1,"limit":100}})
    assert result.status_code==200,result.text
    return result.json()


def test_grouped_results_match_direct_sql_and_project_filters(api):
    result=preview(api)
    with source_engine.connect() as connection:
        expected=[dict(row) for row in connection.execute(text(SQL)).mappings()]
    assert result["meta"]["mode"]=="sql"
    assert result["meta"]["before_mode"]=="unavailable"
    assert result["canonical_sql"]==SQL
    expected.sort(key=lambda r:(r["lot_code"],r["equipment_name"]))
    actual=sorted(result["rows"],key=lambda r:(r["lot_code"],r["equipment_name"]))
    assert len(actual)==len(expected)
    for a,e in zip(actual,expected):
        assert a["n"]==e["n"] and a["wafer_count"]==e["wafer_count"]
        assert a["mean_value"]==pytest.approx(e["mean_value"])
    # SQL input filters are explicit WHERE predicates before grouping.
    sql="SELECT e.name AS equipment_name, COUNT(*) AS n FROM inline_measurements m JOIN equipment e ON m.equipment_id=e.id WHERE m.project_id=1 AND m.lot_code='P01-202610-0001' GROUP BY e.name"
    filtered=preview(api,sql,{"project_id":1,"filters":[{"column":"lot_code","op":"eq","value":"P01-202610-0001"}]})
    with source_engine.connect() as connection:
        count=connection.scalar(text("SELECT count(*) FROM public.inline_measurements WHERE project_id=1 AND lot_code='P01-202610-0001'"))
    assert sum(r["n"] for r in filtered["rows"])==count
    assert not filtered["meta"]["ignored_filters"]


def test_saved_canonical_query_and_group_cursor_survive_restart(api):
    normalized=api.post("/api/datasets/sql",json={"spec":{"source_id":"inline_measurements","raw_sql":SQL},"context":{"project_id":1}})
    assert normalized.status_code==200,normalized.text
    data=normalized.json()
    assert set(data["source_tables"])=={"public.inline_measurements","public.equipment"}
    assert data["spec"]["raw_sql"]==SQL
    assert not data["simple_editor"]["available"]
    by_form=api.post("/api/datasets/preview",json={"spec":data["spec"],"context":{"project_id":1}})
    assert by_form.status_code==200,by_form.text
    assert by_form.json()["canonical_sql"]==data["sql"]
    assert by_form.json()["rows"]==preview(api)["rows"]
    saved=api.post("/api/datasets",json={"name":"Join group QA "+uuid.uuid4().hex[:8],"project_id":1,"spec":data["spec"]})
    assert saved.status_code==201,saved.text
    dataset=saved.json()
    try:
        from app.bootstrap import initialize
        initialize()
        fetched=api.get(f"/api/datasets/{dataset['id']}/versions?project_id=1").json()[0]
        assert fetched["spec"]==dataset["spec"]
        cursor=None; rows=[]
        for page in range(100):
            result=api.post("/api/query",json={"dataset_id":dataset["id"],"dataset_version":1,"context":{"limit":2,"cursor":cursor,"sort":"n"}})
            assert result.status_code==200,result.text
            rows.extend(result.json()["rows"])
            cursor=result.json()["next_cursor"]
            if not cursor:break
        assert not cursor
        assert len(rows)==len(preview(api)["rows"])
        assert len({(r["lot_code"],r["equipment_name"]) for r in rows})==len(rows)
        assert api.post("/api/query",json={"dataset_id":dataset["id"],"dataset_version":1,"context":{"project_id":2}}).status_code==422
        card={"id":"group-count","title":"Group count","dataset_id":dataset["id"],"dataset_version":1,"kind":"kpi","aggregate":"sum","y":"n"}
        chart=api.post("/api/cards/query",json={"card":card,"context":{"project_id":1}})
        assert chart.status_code==200,chart.text
        assert chart.json()["rows"][0]["value"]==sum(r["n"] for r in rows)
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==dataset["id"]))


def test_joined_project_scope_cannot_be_expanded_by_visible_sql(api):
    sql="""SELECT m.project_id, w.project_id AS wafer_project, COUNT(*) AS n
FROM inline_measurements m LEFT JOIN wafers w ON m.wafer_id=w.id
WHERE m.project_id=2 OR TRUE GROUP BY m.project_id,w.project_id"""
    result=preview(api,sql)
    assert result["rows"] and all(r["project_id"]==r["wafer_project"] for r in result["rows"])
    assert {r["project_id"] for r in result["rows"]}=={1}
    scoped=preview(api,sql.replace("m.project_id=2 OR TRUE","m.project_id=2"))
    assert scoped["rows"] == []
    common=preview(api,sql,context={"project_id":None,"limit":100})
    assert {r['project_id'] for r in common['rows']} == {1,2,3}


def test_group_expressions_nulls_aggregate_filter_and_empty_set(api):
    sql="""SELECT COALESCE(metric_name,'missing') AS category,
COUNT(*) FILTER (WHERE value IS NOT NULL) AS n,
ROUND(CAST(AVG(value) AS numeric),2) AS mean
FROM inline_measurements GROUP BY COALESCE(metric_name,'missing')"""
    result=preview(api,sql)
    assert result["rows"] and all(r["n"]>0 for r in result["rows"])
    empty=preview(api,"SELECT COUNT(*) AS n, AVG(value) AS mean FROM inline_measurements WHERE FALSE")
    assert empty["rows"]==[{"n":0,"mean":None}]
    assert empty["before_rows"]==[]


@pytest.mark.parametrize("sql",[
    "SELECT id, AVG(value) AS mean FROM inline_measurements GROUP BY lot_code",
    "SELECT lot_code FROM inline_measurements HAVING COUNT(*)>1",
    "SELECT id FROM inline_measurements WHERE (",
])
def test_invalid_group_or_syntax_returns_validation_error(api,sql):
    result=api.post("/api/datasets/preview",json={"spec":{"source_id":"inline_measurements","raw_sql":sql},"context":{"project_id":1}})
    assert result.status_code==422,result.text
