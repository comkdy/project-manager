from test_project_datasets import api


def test_generated_groups_preview_and_roundtrip(api):
    expressions = ["lot_id", "metric_name", "AVG(value)"]
    response = api.post("/api/datasets/group-completion", json={
        "expressions": expressions, "input_columns": ["lot_id", "metric_name", "value"],
    })
    assert response.status_code == 200, response.text
    groups = response.json()["group_by"]
    assert groups == ['"lot_id"', '"metric_name"']
    spec = {"source_id": "inline_measurements", "columns": [
        {"name": name, "expression": expression}
        for name, expression in zip(["lot_id", "metric_name", "mean"], expressions)
    ], "group_by": groups}
    preview = api.post("/api/datasets/preview", json={"spec": spec, "context": {"project_id": 1}})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["rows"]
    assert result["meta"]["before_mode"] == "source_sample"
    assert result["spec"]["group_by"] == groups
    by_sql = api.post("/api/datasets/preview", json={
        "spec": {"source_id": "inline_measurements", "raw_sql": result["canonical_sql"]},
        "context": {"project_id": 1},
    })
    assert by_sql.status_code == 200, by_sql.text
    order = lambda row: (row["lot_id"], row["metric_name"])
    assert sorted(by_sql.json()["rows"],key=order) == sorted(result["rows"],key=order)


def test_partial_input_is_advisory_and_input_sizes_are_bounded(api):
    response = api.post("/api/datasets/group-completion", json={
        "expressions": ["lot_id", "AVG("], "input_columns": ["lot_id", "value"],
    })
    assert response.status_code == 200
    assert response.json() == {"complete": False, "group_by": []}
    assert api.post("/api/datasets/group-completion", json={
        "expressions": ["x" * 4001], "input_columns": ["value"],
    }).status_code == 422
