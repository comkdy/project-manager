"""Project query definitions share source tables, but never another project's edits."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, settings
from app.models import Dashboard, DatasetVersion


@pytest.fixture(scope="module")
def api():
    if not settings.demo_mode or not settings.app_database_url:
        pytest.skip("Requires isolated demo PostgreSQL")
    from app.main import app
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(settings,"auth_mode","local_admin")
        with TestClient(app,client=("127.0.0.1",50005)) as client:
            assert client.get("/api/auth/session").json()["status"] == "authenticated"
            yield client


@pytest.fixture
def owned(api):
    spec={"source_id":"inline_measurements","columns":[
        {"name":"id","expression":"id"},{"name":"project_id","expression":"project_id"},
        {"name":"adjusted","expression":"value * 1000"}]}
    response=api.post("/api/datasets",json={"name":"Project scope QA "+uuid.uuid4().hex[:8],"project_id":1,"spec":spec})
    assert response.status_code == 201,response.text
    dataset=response.json()
    try:
        yield dataset
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==dataset["id"]))


def test_catalog_and_version_lists_follow_project_scope(api,owned):
    assert owned["project_id"] == 1
    common=api.get("/api/bootstrap").json()["datasets"]
    one=api.get("/api/bootstrap?project_id=1").json()["datasets"]
    two=api.get("/api/bootstrap?project_id=2").json()["datasets"]
    assert owned["id"] not in {d["id"] for d in common+two}
    assert owned["id"] in {d["id"] for d in one}
    assert not {d["id"] for d in common} & {d["id"] for d in one}
    assert all(d['project_id'] == 1 for d in one)
    assert all(d["project_id"] is None for d in common)
    versions=f"/api/datasets/{owned['id']}/versions"
    assert api.get(versions+"?project_id=1").status_code == 200
    assert api.get(versions+"?project_id=2").status_code == 404
    assert api.get(versions).status_code == 404


def test_owned_dataset_query_cannot_expand_or_change_project(api,owned):
    body={"dataset_id":owned["id"],"dataset_version":1,"context":{"limit":8}}
    result=api.post("/api/query",json=body)
    assert result.status_code == 200,result.text
    assert len(result.json()["rows"]) == 8
    assert all(r["project_id"]==1 for r in result.json()["rows"])
    body["context"]["project_id"]=2
    assert api.post("/api/query",json=body).status_code == 422
    card={"id":"owned","title":"Owned count","dataset_id":owned["id"],"dataset_version":1,"kind":"kpi","aggregate":"count"}
    first=api.post("/api/cards/query",json={"card":card,"context":{"refresh":True}})
    expected=api.post("/api/cards/query",json={"card":dict(card,dataset_id="inline_measurements"),"context":{"project_id":1,"refresh":True}})
    assert first.status_code == expected.status_code == 200
    assert first.json()["rows"] == expected.json()["rows"]
    cached=api.post("/api/cards/query",json={"card":card,"context":{"project_id":1}})
    assert cached.json()["meta"]["cached"] is True
    assert api.post("/api/cards/query",json={"card":card,"context":{"project_id":2}}).status_code == 422


def test_owned_versions_keep_owner_and_pinned_old_projection(api,owned):
    body={"name":owned["name"],"spec":owned["spec"],"project_id":2,"expected_version":1}
    url=f"/api/datasets/{owned['id']}/versions"
    assert api.post(url,json=body).status_code == 422
    body["project_id"]=None
    assert api.post(url,json=body).status_code == 422
    body["project_id"]=1
    body["spec"]["columns"][-1]["expression"]="value * 2000"
    response=api.post(url,json=body)
    assert response.status_code == 201,response.text
    assert response.json()["project_id"] == 1
    old=api.post("/api/query",json={"dataset_id":owned["id"],"dataset_version":1,"context":{"limit":3}}).json()
    new=api.post("/api/query",json={"dataset_id":owned["id"],"dataset_version":2,"context":{"limit":3}}).json()
    for first,second in zip(old["rows"],new["rows"]):
        assert first["id"]==second["id"]
        assert second["adjusted"] == pytest.approx(first["adjusted"]*2)


def test_shared_definition_cannot_be_reassigned_by_project_version(api):
    shared=next(d for d in api.get("/api/bootstrap").json()["datasets"] if d["id"]=="inline_measurements")
    result=api.post("/api/datasets/inline_measurements/versions",json={"name":shared["name"],"spec":shared["spec"],"project_id":1,"expected_version":shared["version"]})
    assert result.status_code == 422


def test_dashboard_rejects_other_project_or_common_usage(api,owned):
    for project in (None,2):
        board=api.get("/api/dashboards",params={} if project is None else {"project_id":project}).json()
        card={"id":"scope-check","title":"Scoped card","dataset_id":owned["id"],"dataset_version":1,"kind":"kpi","aggregate":"count"}
        body={"version":board["version"],"cards":[card],"layout":[{"i":"scope-check","x":0,"y":0,"w":4,"h":3}]}
        result=api.put("/api/dashboards/"+board["id"],json=body)
        assert result.status_code == 422,result.text
        assert api.get("/api/dashboards",params={} if project is None else {"project_id":project}).json()==board


def test_owning_project_dashboard_accepts_project_dataset(api,owned):
    dashboard_id="scope-qa-"+uuid.uuid4().hex[:8]
    with SessionLocal.begin() as db:
        db.add(Dashboard(id=dashboard_id,project_id=1,version=1,cards=[],layout=[]))
    try:
        card={"id":"scope-check","title":"Scoped card","dataset_id":owned["id"],"dataset_version":1,"kind":"kpi","aggregate":"count"}
        response=api.put("/api/dashboards/"+dashboard_id,json={"version":1,"cards":[card],"layout":[{"i":"scope-check","x":0,"y":0,"w":4,"h":3}]})
        assert response.status_code == 200,response.text
        assert response.json()["project_id"]==1 and response.json()["cards"][0]["dataset_id"]==owned["id"]
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Dashboard).where(Dashboard.id==dashboard_id))


def test_unknown_project_rejected_before_saving(api):
    spec={"source_id":"inline_measurements"}
    assert api.post("/api/datasets",json={"name":"Invalid scope","project_id":999999,"spec":spec}).status_code == 404
    assert api.post("/api/datasets/preview",json={"spec":spec,"context":{"project_id":999999}}).status_code == 404
    assert api.get("/api/bootstrap?project_id=999999").status_code == 404


def test_editable_raw_sql_roundtrip_preserves_projection_filters_and_scope(api):
    spec={"source_id":"inline_measurements","columns":[
        {"name":"id","expression":"id"},{"name":"project_id","expression":"project_id"},
        {"name":"adjusted","expression":"value * 1000"}],
        "filters":[{"column":"adjusted","op":"gt","value":95000}]}
    body={"spec":spec,"context":{"project_id":1,"limit":7}}
    form=api.post("/api/datasets/preview",json=body)
    editable=api.post("/api/datasets/sql",json=body)
    assert editable.status_code == 200,editable.text
    sql=editable.json()["sql"]
    assert "SELECT" in sql.upper() and "INLINE_MEASUREMENTS" in sql.upper()
    assert editable.json()["project_id"] == 1
    raw=api.post("/api/datasets/preview",json={"spec":{"source_id":"inline_measurements","raw_sql":sql},"context":body["context"]})
    assert form.status_code == raw.status_code == 200,raw.text
    assert form.json()["rows"] == raw.json()["rows"]
    assert raw.json()["canonical_sql"] == sql
    assert raw.json()["meta"]["before_mode"] == "unavailable"


def test_form_to_raw_contains_keeps_literal_wildcard_escaping(api):
    spec={"source_id":"inline_measurements","columns":[
        {"name":"id","expression":"id"},{"name":"label","expression":"'A_B%\\C'"}],
        "filters":[{"column":"label","op":"contains","value":"_B%"}]}
    context={"project_id":1,"limit":3}
    form=api.post("/api/datasets/preview",json={"spec":spec,"context":context})
    editable=api.post("/api/datasets/sql",json={"spec":spec,"context":context})
    assert editable.status_code == 200,editable.text
    raw=api.post("/api/datasets/preview",json={"spec":{"source_id":"inline_measurements","raw_sql":editable.json()["sql"]},"context":context})
    assert form.status_code == raw.status_code == 200,raw.text
    assert len(form.json()["rows"]) == 3
    assert form.json()["rows"] == raw.json()["rows"]


def test_project_query_blocks_unscoped_shared_dimension(api):
    context={"project_id":1,"limit":5}
    spec={"source_id":"equipment"}
    editable=api.post("/api/datasets/sql",json={"spec":spec,"context":context})
    assert editable.status_code == 200,editable.text
    assert "범위를 넓힐 수 없습니다" in editable.json()["scope_note"]
    assert "WHERE" not in editable.json()["sql"].upper()
    preview=api.post("/api/datasets/preview",json={"spec":spec,"context":context})
    common=api.post("/api/datasets/preview",json={"spec":spec,"context":{"limit":5}})
    assert preview.status_code == 422 and common.status_code == 200
    assert '공통 원천' in preview.json()['detail']


def test_raw_select_star_cannot_override_project_boundary(api):
    spec={"source_id":"inline_measurements","raw_sql":"SELECT *, value * 1000 AS value_scaled FROM public.inline_measurements WHERE project_id = 2 ORDER BY id"}
    preview=api.post("/api/datasets/preview",json={"spec":spec,"context":{"project_id":1,"limit":9}})
    assert preview.status_code == 200,preview.text
    assert preview.json()["rows"] == []
    assert preview.json()['spec']['raw_sql'] == spec['raw_sql']
    created=api.post("/api/datasets",json={"name":"Raw SQL scope QA "+uuid.uuid4().hex[:6],"project_id":1,"spec":spec})
    assert created.status_code == 201,created.text
    dataset=created.json()
    try:
        read=api.post("/api/query",json={"dataset_id":dataset["id"],"dataset_version":1,"context":{"limit":9}})
        assert read.status_code == 200,read.text
        assert read.json()["rows"] == preview.json()["rows"]
        assert api.post("/api/query",json={"dataset_id":dataset["id"],"dataset_version":1,"context":{"project_id":2}}).status_code == 422
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==dataset["id"]))


def test_raw_projection_without_primary_key_keeps_cursor_identity(api):
    spec={"source_id":"inline_measurements","raw_sql":"SELECT value * 10 AS scaled FROM inline_measurements WHERE value IS NOT NULL"}
    created=api.post("/api/datasets",json={"name":"Raw cursor QA "+uuid.uuid4().hex[:6],"project_id":2,"spec":spec})
    assert created.status_code == 201,created.text
    dataset=created.json()
    try:
        body={"dataset_id":dataset["id"],"dataset_version":1,"context":{"limit":4,"sort":"scaled"}}
        first=api.post("/api/query",json=body)
        assert first.status_code == 200,first.text
        assert first.json()["next_cursor"]
        body["context"]["cursor"]=first.json()["next_cursor"]
        second=api.post("/api/query",json=body)
        assert second.status_code == 200,second.text
        assert all(set(row)=={"scaled"} for row in first.json()["rows"]+second.json()["rows"])
        assert first.json()["rows"][-1]["scaled"] <= second.json()["rows"][0]["scaled"]
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id==dataset["id"]))
