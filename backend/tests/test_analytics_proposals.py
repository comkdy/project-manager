"""Confirmation uses isolated SQLite metadata and a stub catalog; no provider/source calls."""
from copy import deepcopy

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select

from app import db as database
from app.analytics import proposals
from app.analytics.chat_contracts import AnalysisCall
from app.analytics.models import AnalysisProposal, AnalysisRun
from app.analytics.proposal_contract import AnalysisProposalView
from app.models import AppProject, DatabaseMount, Source, User
from app.semantic.collector_models import KnowledgeQuestion
from test_analytics_chat import CATALOG, TEXT, call, chat
from test_analytics_service import env


BASE = "/api/analytics/proposals"


@pytest.fixture(autouse=True)
def no_source_catalog(monkeypatch):
    monkeypatch.setattr(proposals, "catalog", lambda db: CATALOG)
    monkeypatch.setattr(proposals, "wake_worker", lambda: None)


def pending(env, plan=None, request=None, **kwargs):
    return proposals.create_proposal(plan or call(), request or chat(), env.actor.id, CATALOG, **kwargs)


def body(proposal, **changes):
    return {"expected_revision": proposal["revision"], "workspace_token": proposal["workspace_token"],
        "project_id": proposal["project_id"], "request": {**proposal["request"], **changes}}


def confirm(env, proposal, payload=None):
    return env.client.post(f"{BASE}/{proposal['proposal_id']}/confirm", json=payload or body(proposal))


def counts(env):
    with env.sessions() as db:
        return (db.scalar(select(func.count()).select_from(AnalysisRun)),
                db.scalar(select(func.count()).select_from(KnowledgeQuestion)))


def test_pending_proposal_is_durable_private_and_has_no_execution_side_effects(env):
    proposal = pending(env, conversation_id="conversation-1", turn_sequence=2)
    assert proposal["status"] == "pending" and proposal["missing"] == []
    assert proposal["request"]["absolute_delta"] == 2 and proposal["evidence"]["absolute_delta"] == TEXT
    AnalysisProposalView.model_validate(proposal)
    assert counts(env) == (0, 0)
    with env.sessions() as db:
        stored = db.get(AnalysisProposal, proposal["proposal_id"])
        assert stored.conversation_id == "conversation-1" and stored.turn_sequence == 2
    fetched = env.client.get(f"{BASE}/{proposal['proposal_id']}")
    assert fetched.status_code == 200 and fetched.json()["proposal_id"] == proposal["proposal_id"]


def test_explicit_project_from_common_chat_restores_and_confirms_with_its_target_scope(env):
    from app.models import ProjectRemoval
    for table in (AppProject.__table__,ProjectRemoval.__table__):
        table.create(env.sessions.kw['bind'],checkfirst=True)
    with env.sessions.begin() as db:
        db.add(AppProject(id=5,connection_key='a',name='XXXA',lot_cds=['XXXA'],active=True))
    proposal=pending(env,request=chat(project_id=5),origin_project_id=None)
    assert proposal['project_scope']['lot_cds']==['XXXA']
    restored=proposals.restore_proposal({'proposal_id':proposal['proposal_id']},env.actor.id,None)
    assert restored['status']=='pending' and restored['project_id']==5
    assert proposals.restore_proposal({'proposal_id':proposal['proposal_id']},env.actor.id,6)['status']=='unavailable'
    response=confirm(env,proposal)
    assert response.status_code==202 and response.json()['request']['_project_scope']['lot_cds']==['XXXA']


def test_confirmation_uses_explicit_form_edits_and_retry_returns_the_same_run(env, monkeypatch):
    proposal = pending(env)
    wakes = []
    def wake_after_commit():
        with env.sessions() as db:
            stored = db.get(AnalysisProposal, proposal["proposal_id"])
            assert stored.status == "confirmed" and db.get(AnalysisRun, stored.run_id)
            wakes.append(stored.run_id)
    monkeypatch.setattr(proposals, "wake_worker", wake_after_commit)
    payload = body(proposal, min_samples=7, absolute_delta=3.25)
    first, second = confirm(env, proposal, payload), confirm(env, proposal, payload)
    assert first.status_code == second.status_code == 202, first.text
    assert first.json()["id"] == second.json()["id"] and counts(env) == (1, 0)
    assert first.json()["request"]["min_samples"] == 7 and first.json()["request"]["absolute_delta"] == 3.25
    assert wakes == [first.json()["id"]]
    restored = proposals.restore_proposal({"proposal_id": proposal["proposal_id"]}, env.actor.id, None)
    assert restored["status"] == "confirmed" and restored["run_id"] == first.json()["id"]
    assert restored["request"]["absolute_delta"] == 3.25 and restored["revision"] == 2
    assert "absolute_delta" not in restored["evidence"] and "min_samples" not in restored["evidence"]
    changed = confirm(env, proposal, body(proposal, absolute_delta=9))
    assert changed.status_code == 409 and counts(env) == (1, 0)


def test_unknown_values_remain_unknown_until_the_user_confirms(env):
    plan = AnalysisCall(kind="analysis", analysis_kind="trend", goal="cause", family="epm", parameter="PARA1")
    proposal = pending(env, plan, chat("최근 감소 원인"))
    assert proposal["request"]["baseline_start"] is None and counts(env) == (0, 0)
    result = confirm(env, proposal)
    assert result.status_code == 202, result.text
    assert result.json()["status"] == "needs_input" and len(result.json()["questions"]) == 4
    again = confirm(env, proposal)
    assert again.json()["id"] == result.json()["id"] and counts(env) == (1, 4)


def test_form_dates_and_policy_can_resolve_missing_conditions_without_model_evidence(env):
    proposal = pending(env, call(), chat("최근 추세"))
    assert "period" in proposal["blockers"]
    explicit = pending(env)["request"]
    result = confirm(env, proposal, {**body(proposal), "request": explicit})
    assert result.status_code == 202 and result.json()["status"] == "queued", result.text


@pytest.mark.parametrize("blocker", ["analysis_scope", "analysis_method"])
def test_confirmation_cannot_silently_remove_unsupported_scope_or_method(env, blocker):
    proposal = pending(env, call(unresolved=[blocker]))
    result = confirm(env, proposal)
    assert result.status_code == 202 and result.json()["status"] == "needs_input", result.text
    with env.sessions() as db:
        assert f"analysis:{blocker}" in {q.subject for q in db.scalars(select(KnowledgeQuestion))}


def test_new_explicit_target_resolves_ambiguity_but_unknown_target_never_runs(env):
    proposal = pending(env, call(parameter="PARA999"))
    assert "analysis_target" in proposal["blockers"]
    valid = confirm(env, proposal, body(proposal, parameter="PARA1"))
    assert valid.status_code == 202 and valid.json()["status"] == "queued", valid.text
    another = pending(env)
    invalid = confirm(env, another, body(another, parameter="PARA999"))
    assert invalid.status_code == 202 and invalid.json()["status"] == "needs_input"


def test_missing_thresholds_keep_existing_descriptive_result_and_question_behavior(env):
    proposal = pending(env, call(min_samples=None, absolute_delta=None))
    assert proposal["criteria_missing"] == ["change_criteria"] and proposal["warnings"]
    result = confirm(env, proposal)
    assert result.status_code == 202 and result.json()["status"] == "queued"
    assert len(result.json()["questions"]) == 1


@pytest.mark.parametrize("changes", [{"purpose": "validation"}, {"project_id": 999}, {"kind": "statistics",
    "baseline_start": None, "baseline_end": None, "current_start": None, "current_end": None,
    "retest_policy": None, "min_samples": None, "absolute_delta": None}])
def test_fixed_analysis_identity_and_scope_cannot_be_changed(env, changes):
    proposal = pending(env)
    response = confirm(env, proposal, body(proposal, **changes))
    assert response.status_code == 422 and counts(env) == (0, 0), response.text


@pytest.mark.parametrize("field,value", [("baseline_start", "2026-10-06T00:00:00Z"),
    ("retest_policy", "invented_policy"), ("absolute_delta", -1), ("min_samples", 0), ("_project_scope", {"id": 5})])
def test_invalid_or_unknown_form_fields_never_create_jobs(env, field, value):
    proposal = pending(env)
    response = confirm(env, proposal, body(proposal, **{field: value}))
    assert response.status_code == 422 and counts(env) == (0, 0)


@pytest.mark.parametrize("tamper", ["revision", "body_token", "active_token", "schema", "owner", "disabled", "role", "database"])
def test_confirmation_rechecks_identity_permission_database_and_snapshot(env, tamper):
    proposal = pending(env)
    payload = body(proposal)
    if tamper == "revision":
        payload["expected_revision"] += 1
    elif tamper == "body_token":
        payload["workspace_token"] = "other"
    elif tamper in {"owner", "disabled", "role", "schema"}:
        with env.sessions.begin() as db:
            if tamper == "owner":
                db.add(User(id="another-admin", username="another-admin", role="admin", active=True))
                env.actor.id = "another-admin"
            elif tamper == "disabled":
                db.get(User, "admin").active = False
            elif tamper == "role":
                db.get(User, "admin").role = "editor"
            else:
                source = db.get(Source, "a-epm")
                source.columns = source.columns + [{"name": "new_column", "type": "text"}]
    if tamper in {"active_token", "database"}:
        binding = database.SourceBinding(None, "b" if tamper == "database" else "a", revision=100)
        with database.use_source(binding):
            response = confirm(env, proposal, payload)
    else:
        response = confirm(env, proposal, payload)
    assert response.status_code in {403, 404, 409} and counts(env) == (0, 0), response.text


def test_project_scope_and_project_parameter_are_checked_again(env):
    with env.sessions() as db:
        AppProject.__table__.create(db.bind, checkfirst=True)
    with env.sessions.begin() as db:
        db.add(AppProject(id=5, connection_key="a", name="공정", lot_cds=["LOT-A"]))
    proposal = pending(env, request=chat(project_id=5))
    assert proposal["project_scope"]["lot_cds"] == ["LOT-A"]
    assert env.client.get(f"{BASE}/{proposal['proposal_id']}").status_code == 409
    assert env.client.get(f"{BASE}/{proposal['proposal_id']}?project_id=5").status_code == 200
    with env.sessions.begin() as db:
        db.get(AppProject, 5).lot_cds = ["LOT-B"]
    response = confirm(env, proposal)
    assert response.status_code == 409 and counts(env) == (0, 0)


def test_failed_confirmation_rolls_back_claim_and_can_be_retried(env, monkeypatch):
    proposal = pending(env)
    real_create = proposals.create_run
    def fail(*args, **kwargs):
        raise HTTPException(429, "대기열 한도")
    monkeypatch.setattr(proposals, "create_run", fail)
    assert confirm(env, proposal).status_code == 429
    with env.sessions() as db:
        stored = db.get(AnalysisProposal, proposal["proposal_id"])
        assert stored.status == "pending" and stored.revision == 1 and stored.run_id is None
    monkeypatch.setattr(proposals, "create_run", real_create)
    assert confirm(env, proposal).status_code == 202 and counts(env) == (1, 0)


def test_history_restore_uses_server_values_and_masks_another_owner(env):
    proposal = pending(env)
    forged = {**deepcopy(proposal), "request": {"absolute_delta": 999}, "status": "confirmed", "run_id": "fake"}
    restored = proposals.restore_proposal(forged, "admin", None)
    assert restored["status"] == "pending" and restored["request"]["absolute_delta"] == 2 and restored["run_id"] is None
    hidden = proposals.restore_proposal(forged, "reader", None)
    assert hidden["status"] == "unavailable" and hidden["request"] is None and hidden["evidence"] == {}


def test_scope_change_during_confirmation_rolls_back_run_and_claim(env, monkeypatch):
    proposal = pending(env)
    real_create = proposals.create_run
    def changed(db, request, user, **kwargs):
        run = real_create(db, request, user, **kwargs)
        run.source_snapshot = {**run.source_snapshot, "epm": {**run.source_snapshot["epm"], "table_name": "changed"}}
        return run
    monkeypatch.setattr(proposals, "create_run", changed)
    response = confirm(env, proposal)
    assert response.status_code == 409 and counts(env) == (0, 0)
    with env.sessions() as db:
        assert db.get(AnalysisProposal, proposal["proposal_id"]).status == "pending"


def test_captured_workspace_cannot_confirm_after_database_activation_changes(env):
    with env.sessions() as db:
        DatabaseMount.__table__.create(db.bind, checkfirst=True)
    with env.sessions.begin() as db:
        db.add(DatabaseMount(id=1, profile_id="a", revision=1))
    captured = database.SourceBinding(env.binding.engine, "a", profile_id="a", revision=1, ignored_tables=())
    with database.use_source(captured):
        proposal = pending(env)
        with env.sessions.begin() as db:
            db.get(DatabaseMount, 1).revision = 2
        response = confirm(env, proposal)
    assert response.status_code == 409 and counts(env) == (0, 0)


def test_source_catalog_runs_before_shared_locks_and_confirmed_retry_skips_it(env, monkeypatch):
    proposal = pending(env)
    events = []
    def source_catalog(db):
        assert not events, "source reads must not block run creation, worker claims or DB activation"
        events.append("catalog")
        return CATALOG
    monkeypatch.setattr(proposals, "catalog", source_catalog)
    monkeypatch.setattr(proposals, "serialize_creation", lambda db: events.append("creation_lock"))
    monkeypatch.setattr(proposals, "lock_active_workspace", lambda db: events.append("workspace_lock"))
    first = confirm(env, proposal)
    assert first.status_code == 202, first.text
    assert events == ["catalog", "creation_lock", "workspace_lock"]
    events.clear()
    monkeypatch.setattr(proposals, "catalog", lambda db: pytest.fail("confirmed retries must not read source rows"))
    retry = confirm(env, proposal)
    assert retry.status_code == 202 and retry.json()["id"] == first.json()["id"]
    assert events == ["creation_lock", "workspace_lock"] and counts(env) == (1, 0)


@pytest.mark.parametrize("change", ["permission", "schema", "proposal_revision"])
def test_authority_and_snapshot_are_rechecked_after_slow_catalog(env, monkeypatch, change):
    proposal = pending(env)
    def source_catalog(db):
        with env.sessions.begin() as editor:
            if change == "permission":
                editor.get(User, "admin").active = False
            elif change == "schema":
                source = editor.get(Source, "a-epm")
                source.columns = source.columns + [{"name": "added_during_read", "type": "text"}]
            else:
                editor.get(AnalysisProposal, proposal["proposal_id"]).revision += 1
        return CATALOG
    monkeypatch.setattr(proposals, "catalog", source_catalog)
    response = confirm(env, proposal)
    assert response.status_code == (403 if change == "permission" else 409), response.text
    assert counts(env) == (0, 0)
