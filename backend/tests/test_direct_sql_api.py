import uuid

import pytest
from sqlalchemy import delete, event

from app.db import SessionLocal, source_engine
from app.models import DatasetVersion
from test_raw_sql_api import api


def preview(api, sql, **context):
    return api.post("/api/datasets/preview", json={
        "spec": {"source_id": "inline_measurements", "raw_sql": sql},
        "editor_mode": "sql", "context": context,
    })


def test_dataset_responses_include_all_join_sources_and_selected_version(api):
    created = None
    sql = 'SELECT m.id FROM public.inline_measurements m JOIN public.equipment e ON m.equipment_id=e.id'
    expected = ['public.inline_measurements', 'public.equipment']
    try:
        result = api.post('/api/datasets', json={'name':'QA source labels',
                         'spec':{'source_id':'inline_measurements', 'raw_sql':sql}})
        assert result.status_code == 201, result.text
        created = result.json()['id']
        assert result.json()['source_tables'] == expected
        listed = next(d for d in api.get('/api/bootstrap').json()['datasets'] if d['id'] == created)
        assert listed['source_tables'] == expected
        updated = api.post(f'/api/datasets/{created}/versions', json={'name':'QA source labels',
            'expected_version':1, 'spec':{'source_id':'inline_measurements', 'raw_sql':'SELECT id FROM inline_measurements'}})
        assert updated.status_code == 201, updated.text
        assert updated.json()['source_tables'] == ['public.inline_measurements']
        assert api.get(f'/api/datasets/{created}/latest').json()['source_tables'] == ['public.inline_measurements']
        versions = api.get(f'/api/datasets/{created}/versions').json()
        assert next(d for d in versions if d['version'] == 1)['source_tables'] == expected
    finally:
        if created:
            with SessionLocal() as db:
                db.execute(delete(DatasetVersion).where(DatasetVersion.id == created))
                db.commit()


def test_editor_executes_original_text_and_preserves_order(api):
    sql = """-- keep the query exactly as written
SELECT id, ':value' AS literal, '100%' AS percent, id % 2 AS remainder
FROM public.inline_measurements
WHERE project_id = 2
ORDER BY id DESC LIMIT 4; -- trailing comment"""
    observed = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        observed.append(statement)
    event.listen(source_engine, "before_cursor_execute", capture)
    try:
        response = preview(api, sql, limit=100, sort="id", direction="asc",
                           filters=[{"column": "id", "op": "eq", "value": -1}])
    finally:
        event.remove(source_engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    result = response.json()
    assert observed.count(sql) == 1
    assert result["canonical_sql"] == result["sql"] == result["spec"]["raw_sql"] == sql
    assert len(result["rows"]) == 4
    assert [r["id"] for r in result["rows"]] == sorted([r["id"] for r in result["rows"]], reverse=True)
    assert all(r["literal"] == ":value" and r["percent"] == "100%" for r in result["rows"])
    assert not result["simple_editor"]["available"]


def test_legacy_dataset_preserves_editor_sql_and_enforces_project_scope(api):
    dataset = next(d for d in api.get("/api/bootstrap").json()["datasets"] if d["id"] == "inline_measurements")
    sql = api.post("/api/datasets/sql", json={"spec":dataset["spec"], "context":{"project_id":2}}).json()["sql"]
    assert "s.lot_code" in sql and '"s"' not in sql
    observed = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        observed.append(statement)
    event.listen(source_engine, "before_cursor_execute", capture)
    try:
        response = api.post("/api/query", json={"dataset_id":dataset["id"], "dataset_version":dataset["version"],
                                               "context":{"project_id":2,"limit":100}})
    finally:
        event.remove(source_engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert any('"project_id" = 2' in statement for statement in observed)
    assert all(r["project_id"] == 2 for r in response.json()["rows"])


@pytest.mark.parametrize("sql", [
    "WITH sample AS (SELECT id FROM inline_measurements LIMIT 3) SELECT * FROM sample ORDER BY id",
    "SELECT * FROM (SELECT id FROM inline_measurements ORDER BY id DESC LIMIT 3) q ORDER BY id",
    "SELECT metric_name, AVG(value) AS mean FROM inline_measurements GROUP BY metric_name HAVING COUNT(*)>1",
    "SELECT m.id, e.name FROM inline_measurements m LEFT JOIN equipment e ON m.equipment_id=e.id LIMIT 3",
    "SELECT id, ROW_NUMBER() OVER (ORDER BY id DESC) AS position FROM inline_measurements ORDER BY id DESC LIMIT 3",
    "SELECT MIN(id) AS id FROM inline_measurements UNION ALL SELECT MAX(id) AS id FROM inline_measurements",
])
def test_full_read_sql_is_executable_while_simple_editor_is_disabled(api, sql):
    response = preview(api, sql)
    assert response.status_code == 200, response.text
    assert response.json()["rows"]
    assert not response.json()["simple_editor"]["available"]


def test_bidirectional_editor_connection_preserves_where_and_group(api):
    sql = "SELECT metric_name, AVG(value) AS mean FROM inline_measurements WHERE project_id=2 AND value>0 GROUP BY metric_name"
    checked = api.post("/api/datasets/simple-editor", json={"source_id": "inline_measurements", "sql": sql})
    state = checked.json()
    assert checked.status_code == 200 and state["available"]
    spec = state["spec"]
    assert {f["column"] for f in spec["filters"]} == {"project_id", "value"}
    spec["columns"][1] = {"name": "total", "expression": "SUM(value)"}
    exported = api.post("/api/datasets/sql", json={"spec": spec, "editor_mode": "simple", "context": {"project_id": 1}})
    assert exported.status_code == 200, exported.text
    data = exported.json()
    assert data["simple_editor"]["available"]
    assert "SUM(" in data["sql"] and "HAVING" not in data["sql"]
    assert data["sql"].count("project_id") == 1
    form = api.post("/api/datasets/preview", json={"spec": spec, "editor_mode": "simple", "context": {"project_id": 1}})
    raw = preview(api, data["sql"], project_id=1)
    assert form.status_code == raw.status_code == 200, form.text + raw.text
    assert form.json()["rows"] == raw.json()["rows"]
    assert form.json()["spec"]["raw_sql"] == data["sql"]
    assert api.post("/api/datasets/simple-editor", json={"source_id": "inline_measurements", "sql": sql + " HAVING COUNT(*)>1"}).json()["available"] is False
    assert api.post("/api/datasets/simple-editor", json={"source_id": "inline_measurements", "sql": sql}).json()["available"] is True


def test_simple_in_filter_matches_exact_values_and_roundtrips(api):
    sample = preview(api, 'SELECT id FROM inline_measurements ORDER BY id LIMIT 3')
    assert sample.status_code == 200, sample.text
    ids = [row['id'] for row in sample.json()['rows']]
    assert len(ids) == 3
    selected = [ids[0], ids[2]]
    spec = {"source_id": "inline_measurements", "filters": [{"column": "id", "op": "in", "value": selected}]}
    response = api.post('/api/datasets/preview', json={'spec': spec, 'editor_mode': 'simple', 'context': {}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert sorted(row['id'] for row in result['rows']) == selected
    assert f' IN ({selected[0]}, {selected[1]})' in result['canonical_sql'] and 'ILIKE' not in result['canonical_sql']
    assert result['simple_editor']['spec']['filters'] == spec['filters']
    raw = preview(api, result['canonical_sql'])
    assert raw.status_code == 200, raw.text
    assert raw.json()['rows'] == result['rows']

    spec['filters'] = [{"column": "metric_name", "op": "in", "value": ['thickness', 'width']}]
    response = api.post('/api/datasets/preview', json={'spec': spec, 'editor_mode': 'simple', 'context': {}})
    assert response.status_code == 200, response.text
    assert response.json()['rows']
    assert all(row['metric_name'] in ['thickness', 'width'] for row in response.json()['rows'])
    spec['filters'][0]['value'] = ['thick']
    assert api.post('/api/datasets/preview', json={'spec': spec, 'editor_mode': 'simple', 'context': {}}).json()['rows'] == []
    fragment = str(ids[0])[-2:]
    spec['filters'] = [{"column": "id", "op": "contains", "value": fragment}]
    response = api.post('/api/datasets/preview', json={'spec': spec, 'editor_mode': 'simple', 'context': {}})
    assert response.status_code == 200, response.text
    assert response.json()['rows'] and all(fragment in str(row['id']) for row in response.json()['rows'])


def test_not_in_excludes_only_listed_values_in_dataset_and_card(api):
    sample = preview(api, 'SELECT id FROM inline_measurements ORDER BY id LIMIT 3')
    assert sample.status_code == 200, sample.text
    ids = [row['id'] for row in sample.json()['rows']]
    assert len(ids) == 3
    filters = [{'column': 'id', 'op': 'in', 'value': ids},
               {'column': 'id', 'op': 'not_in', 'value': [ids[0], ids[2]]}]
    spec = {'source_id': 'inline_measurements', 'filters': filters}
    response = api.post('/api/datasets/preview', json={'spec': spec, 'editor_mode': 'simple', 'context': {}})
    assert response.status_code == 200, response.text
    data = response.json()
    assert [row['id'] for row in data['rows']] == [ids[1]]
    assert f' NOT IN ({ids[0]}, {ids[2]})' in data['canonical_sql']
    assert data['simple_editor']['spec']['filters'] == filters
    assert preview(api, data['canonical_sql']).json()['rows'] == data['rows']

    dataset = next(d for d in api.get('/api/bootstrap').json()['datasets'] if d['id'] == 'inline_measurements')
    card = {'id': 'not-in-preview', 'title': 'NOT IN', 'dataset_id': dataset['id'], 'dataset_version': dataset['version'],
            'kind': 'kpi', 'aggregate': 'count', 'filters': filters}
    result = api.post('/api/cards/query', json={'card': card, 'context': {'refresh': True}})
    assert result.status_code == 200, result.text
    assert result.json()['rows'] == [{'value': 1}]

    spec['filters'] = [{'column': 'id', 'op': 'in', 'value': ids},
                       {'column': 'metric_name', 'op': 'not_in', 'value': ['thick', "x'); DROP TABLE wafers;--"]}]
    result = api.post('/api/datasets/preview', json={'spec': spec, 'editor_mode': 'simple', 'context': {}})
    assert result.status_code == 200, result.text
    assert sorted(row['id'] for row in result.json()['rows']) == ids
    spec['filters'][1]['value'] = list({row['metric_name'] for row in result.json()['rows']})
    result = api.post('/api/datasets/preview', json={'spec': spec, 'editor_mode': 'simple', 'context': {}})
    assert result.status_code == 200, result.text
    assert result.json()['rows'] == []


def test_saved_sql_versions_pagination_chart_and_empty_metadata(api):
    sql = "SELECT id, ':value' AS label FROM inline_measurements ORDER BY id DESC LIMIT 8; -- saved"
    saved = api.post("/api/datasets", json={"name": "Direct SQL QA " + uuid.uuid4().hex[:8],
                     "project_id": 1, "spec": {"source_id": "inline_measurements", "raw_sql": sql}})
    assert saved.status_code == 201, saved.text
    dataset = saved.json()
    try:
        assert dataset["spec"]["raw_sql"] == sql
        fetched = api.get(f"/api/datasets/{dataset['id']}/versions?project_id=1").json()[0]
        assert fetched["spec"]["raw_sql"] == sql
        body = {"dataset_id": dataset["id"], "dataset_version": 1, "context": {"limit": 3}}
        rows, cursor = [], None
        for _ in range(4):
            body["context"]["cursor"] = cursor
            response = api.post("/api/query", json=body)
            assert response.status_code == 200, response.text
            rows += response.json()["rows"]
            cursor = response.json()["next_cursor"]
            if not cursor:
                break
        assert len(rows) == 8 and len({r["id"] for r in rows}) == 8
        assert [r["id"] for r in rows] == sorted([r["id"] for r in rows], reverse=True)
        card = {"id": "direct-sql", "title": "SQL", "kind": "kpi", "aggregate": "count",
                "dataset_id": dataset["id"], "dataset_version": 1,
                "filters": [{"column": "label", "op": "eq", "value": ":value"}]}
        chart = api.post("/api/cards/query", json={"card": card, "context": {"refresh": True}})
        assert chart.status_code == 200, chart.text
        assert chart.json()["rows"] == [{"value": 8}]
        body["context"] = {"project_id": 2}
        assert api.post("/api/query", json=body).status_code == 422
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(DatasetVersion).where(DatasetVersion.id == dataset["id"]))
    empty = preview(api, "SELECT id, value FROM inline_measurements WHERE FALSE")
    assert empty.status_code == 200, empty.text
    assert empty.json()["rows"] == []
    assert [c["name"] for c in empty.json()["columns"]] == ["id", "value"]
