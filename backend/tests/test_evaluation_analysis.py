"""Scoped case execution reuses the existing lifecycle and approval surfaces."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db as database, query
from app.analytics import service, worker
from app.analytics.models import AnalysisRun, migrate_analytics
from app.evaluation_analysis import Execute, Search, Settings, _compile
from app.evaluation_analysis_router import router
from app.models import DatasetVersion, Source, User, utcnow
from app.semantic.models import KnowledgeItem
from test_evaluation_cases import publish
from test_semantic_collection import env

BASE = "/api/evaluation-analysis"
COLS = [{"name": "id", "type": "integer"}, {"name": "recipe", "type": "text"},
        {"name": "alpha", "type": "double precision"}, {"name": "beta", "type": "double precision"},
        {"name": "parameter", "type": "text"}, {"name": "operation", "type": "text"},
        {"name": "end_dt_tm", "type": "timestamp with time zone"}]


@pytest.fixture
def setup(env, monkeypatch):
    with env.sessions.begin() as db:
        migrate_analytics(db.bind)
        db.get(Source, "a-src").columns = COLS
        db.get(DatasetVersion, ("a-data", 1)).columns = COLS
        db.add(User(id="editor", username="editor", role="editor", name="편집자"))
    monkeypatch.setattr(query, "SessionLocal", env.sessions)
    monkeypatch.setattr(query, "_schema_types", lambda compiled: compiled["columns"])
    monkeypatch.setattr(worker, "wake_worker", lambda: None)
    env.client.app.include_router(router)
    _, approval = publish(env, monkeypatch)
    env.reference = {"id": approval["item_id"], "version": approval["item_version"]}
    return env


def body(env, **changes):
    return {"case_ref": env.reference, "suggestion_id": env.reference["id"] + ":recheck",
            "search": {"query": "관련 사례를 찾아줘", "dataset_id": "a-data"}, **changes}


def prepare(env, **changes):
    response = env.client.post(BASE + "/prepare", json=body(env, **changes))
    assert response.status_code == 201, response.text
    return response.json()


def execute(env, run, settings=None):
    return env.client.post(BASE + f"/{run['id']}/execute", json={"expected_revision": run["revision"],
        "workspace_token": run["workspace_token"], "settings": settings or {
            "method": "group_compare", "group_column": "recipe", "metrics": ["alpha"],
            "baseline": "Recipe1", "comparison": "Recipe2"}})


def fake_result(stored):
    return {"method": "group_compare", "method_version": "evaluation-dataset-v1", "captured_at": utcnow().isoformat(),
            "row_count": 4, "groups": [], "deltas": [], "warnings": [], "knowledge_eligible": True,
            "summary": "Recipe1 alpha 평균 2. Recipe2 alpha 평균 4. 차이 2. 인과관계는 확인하지 않았습니다."}


def test_prepare_does_not_compute_or_publish_and_confirm_is_idempotent(setup):
    env = setup
    run = prepare(env)
    assert run["status"] == "needs_input" and run["result"] is None and run["review"] is None
    assert {c["name"] for c in run["columns"] if c["numeric"]} == {"id", "alpha", "beta"}
    first = execute(env, run)
    assert first.status_code == 202 and first.json()["status"] == "queued", first.text
    second = execute(env, run)
    assert second.status_code == 202 and second.json()["id"] == first.json()["id"]
    assert env.client.get(BASE + f"/{run['id']}").status_code == 200
    with env.sessions() as db:
        assert len(list(db.scalars(select(AnalysisRun)))) == 1
        assert len(list(db.scalars(select(KnowledgeItem)))) == 1


def test_added_source_column_preserves_completed_evidence_only(setup, monkeypatch):
    env = setup
    completed = prepare(env)
    waiting = prepare(env)
    assert execute(env, completed).status_code == 202
    monkeypatch.setattr('app.evaluation_compute.compute', lambda c,p,s,cols,stored,checkpoint: fake_result(stored))
    assert worker.tick()
    with env.sessions.begin() as db:
        source = db.get(Source, 'a-src')
        source.columns = [*source.columns, {'name':'eqp_id','type':'text'}]
    assert env.client.get(BASE + '/' + completed['id']).status_code == 200
    assert execute(env, waiting).status_code == 409
    with env.sessions.begin() as db:
        source = db.get(Source, 'a-src')
        source.columns = [{**c,'type':'text'} if c['name']=='alpha' else c for c in source.columns]
    assert env.client.get(BASE + '/' + completed['id']).status_code == 409


def test_worker_runs_selected_filters_period_and_records_snapshot(setup, monkeypatch):
    env = setup
    search = body(env)["search"] | {"card": {"dataset_id": "a-data", "dataset_version": 1, "title": "선택 카드",
        "filters": [{"column": "operation", "op": "eq", "value": "A"},
                    {"column": "parameter", "op": "eq", "value": "parameter1"}],
        "period": {"use_dashboard": True, "source_id": "a-src", "column": "end_dt_tm"}},
        "period_start": {"year": 2026, "week": 40}}
    run = prepare(env, search=search)
    assert run["scope"]["period"]["column"] == "end_dt_tm"
    assert execute(env, run).status_code == 202
    captured = []
    def calculate(cte, params, settings, columns, stored, checkpoint):
        captured.append((cte, params, stored))
        checkpoint()
        assert params["c_0"] == "A" and params["c_1"] == "parameter1"
        assert params["dashboard_period_start"].startswith("2026-09-28")
        assert stored["_evaluation_dataset"]["version"] == 1
        return fake_result(stored)
    monkeypatch.setattr("app.evaluation_compute.compute", calculate)
    assert worker.tick()
    current = env.client.get(BASE + f"/{run['id']}").json()
    assert current["status"] == "completed" and current["result"]["row_count"] == 4
    assert current["source_watermark"]["dataset_version"] == 1 and len(captured) == 1


@pytest.mark.parametrize("change", ["version", "source", "role", "case", "binding"])
def test_confirm_rechecks_every_pinned_scope(setup, change):
    env = setup
    run = prepare(env)
    with env.sessions.begin() as db:
        if change == "version":
            old = db.get(DatasetVersion, ("a-data", 1))
            db.add(DatasetVersion(id=old.id, version=2, name=old.name, spec=old.spec, columns=old.columns))
        elif change == "source":
            db.get(Source, "a-src").available = False
        elif change == "role":
            db.get(User, "admin").role = "editor"
        elif change == "case":
            db.get(KnowledgeItem, env.reference["id"]).published_version = None
        else:
            db.get(Source, "a-src").project_column = "id"
    assert execute(env, run).status_code == 409
    with env.sessions() as db:
        assert db.get(AnalysisRun, run["id"]).status == "needs_input"


def test_permissions_wrong_dataset_and_unknown_column_do_not_run(setup):
    env = setup
    run = prepare(env)
    env.actor.id, env.actor.role = "editor", "editor"
    assert env.client.get(BASE + f"/{run['id']}").status_code == 403
    env.actor.id, env.actor.role = "reader", "viewer"
    assert env.client.post(BASE + "/prepare", json=body(env)).status_code == 403
    env.actor.id, env.actor.role = "admin", "admin"
    mismatch = body(env)["search"] | {"card": {"dataset_id": "b-data", "dataset_version": 1}}
    assert env.client.post(BASE + "/prepare", json=body(env, search=mismatch)).status_code == 422
    assert execute(env, run, {"method": "correlation", "metrics": ["alpha", "recipe"]}).status_code == 422
    assert execute(env, run, {"method": "group_compare", "group_column": "missing", "metrics": ["alpha"]}).status_code == 422


def test_prepare_blocks_unapplied_filters_and_ambiguous_period(setup):
    env = setup
    search = body(env)["search"] | {"card": {"dataset_id": "a-data", "dataset_version": 1,
        "filters": [{"column": "unknown", "op": "eq", "value": "x"}]}}
    assert env.client.post(BASE + "/prepare", json=body(env, search=search)).status_code == 422
    with env.sessions.begin() as db:
        source = db.get(Source, "a-src")
        source.columns = COLS + [{"name": "other_time", "type": "timestamp"}]
    # Explicitly disabled card period remains disabled, even with a dashboard start.
    compiled, cte, params, period = _compile(Search.model_validate(body(env)["search"] | {
        "period_start": {"year": 2026, "week": 40}, "card": {"dataset_id": "a-data", "dataset_version": 1,
        "period": {"use_dashboard": False}}}))
    assert period is None and "dashboard_period_start" not in params


def test_failure_cancel_retry_use_existing_job_controls(setup, monkeypatch):
    from app.activity_contract import ActivityActionRequest
    from app.job_actions import analysis_action
    env = setup
    run = prepare(env)
    queued = execute(env, run).json()
    action = {"target": {"kind": "analysis_run", "id": run["id"], "project_id": None},
              "expected_revision": queued["revision"], "action": "cancel", "workspace_token": queued["workspace_token"]}
    cancelled = analysis_action(ActivityActionRequest.model_validate(action), env.actor)
    assert cancelled.status == "cancelled"
    with env.sessions() as db:
        original = db.get(AnalysisRun, run["id"])
        action.update(action="retry", expected_revision=original.revision)
    retried = analysis_action(ActivityActionRequest.model_validate(action), env.actor)
    assert retried.status == "queued" and retried.target.id != run["id"]
    assert analysis_action(ActivityActionRequest.model_validate(action), env.actor).target.id == retried.target.id
    def fail(*args):
        raise RuntimeError("never-display-provider-secret")
    monkeypatch.setattr("app.evaluation_compute.compute", fail)
    assert worker.tick()
    failed = env.client.get(BASE + f"/{retried.target.id}").json()
    assert failed["status"] == "failed" and "never-display" not in failed["error"]


def test_cancel_during_compute_discards_results(setup, monkeypatch):
    env = setup
    run = prepare(env)
    execute(env, run)
    def calculate(cte, params, settings, columns, stored, checkpoint):
        with env.sessions.begin() as db:
            row = db.get(AnalysisRun, run["id"])
            row.status = "cancelling"
            row.cancel_requested_at = utcnow()
        return fake_result(stored)
    monkeypatch.setattr("app.evaluation_compute.compute", calculate)
    assert worker.tick()
    current = env.client.get(BASE + f"/{run['id']}").json()
    assert current["status"] == "cancelled" and current["result"] is None


def test_result_review_reuses_knowledge_approval(setup, monkeypatch):
    from test_semantic_collection import approve, draft
    env = setup
    run = prepare(env)
    execute(env, run)
    monkeypatch.setattr("app.evaluation_compute.compute", lambda c, p, s, cols, stored, check: fake_result(stored))
    assert worker.tick()
    done = env.client.get(BASE + f"/{run['id']}").json()
    receipt = env.client.post(BASE + f"/{run['id']}/submit-review", json={"expected_revision": done["revision"],
        "workspace_token": done["workspace_token"], "note": "수치 확인"})
    assert receipt.status_code == 201, receipt.text
    d = draft(env)
    assert d["proposal"]["content"]["details"]["analysis_result"] == done["result"]["summary"]
    response = approve(env, d)
    assert response.status_code == 200, response.text
    review = env.client.get(BASE + f"/{run['id']}").json()["review"]
    assert review["status"] == "approved"


def test_correlations_require_two_distinct_metrics_and_comparison_pair():
    from pydantic import ValidationError
    for value in [{"method": "correlation", "metrics": ["alpha"]},
                  {"method": "correlation", "metrics": ["alpha", "alpha"]},
                  {"method": "group_compare", "group_column": "recipe", "metrics": ["alpha"], "baseline": "R1"}]:
        with pytest.raises(ValidationError):
            Settings.model_validate(value)


def test_mounted_worker_preserves_revision_and_rejects_mount_change(setup, monkeypatch):
    from app.models import DatabaseMount, DatabaseProfile
    env = setup
    with env.sessions.begin() as db:
        DatabaseMount.__table__.create(db.bind)
        db.add(DatabaseProfile(id="a", host="synthetic", port=5432, database="synthetic",
                               username="synthetic", encrypted_url="unused"))
        db.add(DatabaseMount(id=1, profile_id="a", revision=7))
    monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "a", "a", 7, ignored_tables=()))
    revisions = []
    def bound(profile, revision=0):
        revisions.append(revision)
        return database.SourceBinding(None, profile.id, profile.id, revision, ignored_tables=())
    monkeypatch.setattr(worker, "binding_for", bound)
    run = prepare(env)
    execute(env, run)
    monkeypatch.setattr("app.evaluation_compute.compute", lambda c, p, s, cols, stored, check: fake_result(stored))
    assert worker.tick()
    assert revisions == [7]
    assert env.client.get(BASE + f"/{run['id']}").json()["status"] == "completed"
    # Returning to the same DB must not discard durable, reviewed evidence.
    with env.sessions.begin() as db:
        db.get(DatabaseMount, 1).revision = 8
    monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "a", "a", 8, ignored_tables=()))
    restored = env.client.get(BASE + f"/{run['id']}")
    assert restored.status_code == 200 and restored.json()["workspace_token"] == "a:8"
    reviewed = env.client.post(BASE + f"/{run['id']}/submit-review", json={
        "expected_revision": restored.json()["revision"], "workspace_token": "a:8"})
    assert reviewed.status_code == 201, reviewed.text
    second = prepare(env)
    execute(env, second)
    def change_mount(c, p, s, cols, stored, check):
        with env.sessions.begin() as db:
            db.get(DatabaseMount, 1).revision = 9
        return fake_result(stored)
    monkeypatch.setattr("app.evaluation_compute.compute", change_mount)
    assert worker.tick()
    with env.sessions() as db:
        current = db.get(AnalysisRun, second["id"])
        assert current.status == "failed" and current.result is None


@pytest.mark.parametrize("sql", ["SELECT id, random() AS alpha FROM public.measurements",
                                "SELECT id, alpha FROM public.measurements TABLESAMPLE SYSTEM (10)"])
def test_nondeterministic_datasets_remain_editable_but_cannot_be_analysed(setup, sql):
    from app.direct_sql import parse_query
    env = setup
    assert parse_query(sql) is not None  # Existing query management is unaffected.
    with env.sessions.begin() as db:
        dataset = db.get(DatasetVersion, ("a-data", 1))
        dataset.spec = {"source_id": "a-src", "raw_sql": sql}
        dataset.columns = [{"name": "id", "type": "integer"}, {"name": "alpha", "type": "double precision"}]
    response = env.client.post(BASE + "/prepare", json=body(env))
    assert response.status_code == 422 and "반복 조회마다" in response.json()["detail"]
