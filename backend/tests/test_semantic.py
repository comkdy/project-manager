"""Isolated metadata tests; never touch the running app's records or source rows."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database
from app.auth import get_current_user, require_admin
from app.models import Audit, Source
from app.semantic.migration import migrate_semantic
from app.semantic.collector_models import KnowledgeAnswer, KnowledgeQuestion
from app.semantic.models import BindingVersion, KnowledgeVersion, SemanticBase, SemanticMigration
from app.semantic.router import router


@pytest.fixture
def semantic(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (Source.__table__, Audit.__table__):
        table.create(engine)
    migrate_semantic(engine)
    migrate_semantic(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "db-a", revision=1, ignored_tables=()))
    physical = [{"schema_name": "public", "table_name": "measurements",
                 "columns": [{"name": "id", "type": "integer"}, {"name": "status", "type": "text"},
                             {"name": "amount", "type": "numeric"}], "primary_key": "id"}]
    from app import source_discovery
    monkeypatch.setattr(source_discovery, "database_tables", lambda **kwargs: physical)
    with sessions.begin() as db:
        for key in ("db-a", "db-b"):
            db.add(Source(id=key+"-source", connection_key=key, name="Measurements", schema_name="public",
                          table_name="measurements", primary_key="id", project_column=None, columns=physical[0]["columns"]))
    actor = SimpleNamespace(id="admin", role="admin", active=True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: actor
    # require_admin retains its real role check via get_current_user.
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, engine=engine, physical=physical, actor=actor, monkeypatch=monkeypatch)
    engine.dispose()


def create(env, *, name="검사 용어", kind="term", details=None, aliases=None, unresolved=None, references=None):
    response = env.client.post("/api/semantic/items", json={
        "kind": kind, "scope": "database" if kind in {"query", "data", "relation"} else "common",
        "content": {"name": name, "description": "담당자가 지정한 업무 설명", "aliases": aliases or [],
                    "details": details or {}, "unresolved": unresolved or [], "references": references or []},
    })
    assert response.status_code == 201, response.text
    return response.json()


def approve(env, item):
    response = env.client.post(f"/api/semantic/items/{item['id']}/review", json={
        "expected_revision": item["revision"], "version": item["version"], "decision": "approve", "note": "업무 담당자 검토",
    })
    assert response.status_code == 200, response.text
    return response.json()


def bind(env, item, source="db-a-source", *, columns=None, predicates=None, approve_mapping=True):
    response = env.client.post(f"/api/semantic/items/{item['id']}/bindings", json={
        "definition_version": item["version"], "expected_revision": item["binding"]["revision"] if item["binding"] else 0,
        "content": {"sources": [{"source_id": source, "role": "fact", "columns": columns or ["id", "status"]}],
                    "predicates": predicates or []},
    })
    assert response.status_code == 201, response.text
    result = response.json()
    if approve_mapping:
        response = env.client.post(f"/api/semantic/items/{item['id']}/bindings/review", json={
            "definition_version": result["version"], "expected_revision": result["binding"]["revision"],
            "version": result["binding"]["version"], "decision": "approve", "note": "DB 매핑 검토",
        })
        assert response.status_code == 200, response.text
        result = response.json()
    return result


def lookup(env, query="검사 용어", **kwargs):
    return env.client.post("/api/semantic/context", json={
        "query": query, "connection_key": database.source_database_key(),
        "workspace_token": database.workspace_token(), **kwargs,
    })


def metric_details():
    # Explicit synthetic test rules, never seed these into the application.
    return {"metric_type": "ratio", "formula": "synthetic ratio", "numerator": "test numerator",
            "denominator": "test denominator", "grain": "test row", "unit": "test unit",
            "time_basis": "test time", "time_window": "test window", "timezone": "test zone",
            "exclusions": "explicit test exclusions", "retest_policy": "explicit test rule",
            "deduplication": "test key", "zero_policy": "test null", "aggregation": "test ratio of sums"}


def test_additive_migration_is_idempotent(semantic):
    item = bind(semantic, approve(semantic, create(semantic)))
    changed = semantic.client.post(f"/api/semantic/items/{item['id']}/versions", json={
        "expected_revision": item["revision"], "content": {**item["content"], "description": "검토 대기 중인 수정"}})
    assert changed.status_code == 201, changed.text
    with semantic.sessions.begin() as db:
        db.add(KnowledgeQuestion(id="preserved-question", connection_key="db-a", fingerprint="preserved-question",
               title="기존 업무 질문", kind="term", topic="other", dataset={"id": "existing-dataset"},
               context_source_ids=["db-a-source"], targets=[{"kind": "column", "column": "status", "source_id": "db-a-source"}]))
        db.flush()
        db.add(KnowledgeAnswer(id="preserved-answer", question_id="preserved-question", user_id="admin",
                              text="기존 담당자 답변", targets=[{"kind": "column", "column": "status", "source_id": "db-a-source"}]))

    def snapshot():
        tables = [*SemanticBase.metadata.sorted_tables, Source.__table__, Audit.__table__]
        with semantic.sessions() as db:
            return {table.name: [dict(row) for row in db.execute(select(table).order_by(*table.primary_key)).mappings()]
                    for table in tables}

    before = snapshot()
    migrate_semantic(semantic.engine)
    migrate_semantic(semantic.engine)
    assert snapshot() == before
    with semantic.sessions() as db:
        assert list(db.scalars(select(SemanticMigration.version).order_by(SemanticMigration.version))) == [1, 2, 3, 4, 5]


def test_unknown_definitions_remain_draft_and_cannot_be_approved(semantic):
    item = create(semantic, kind="metric", name="수율", unresolved=["재검사 규칙 미정"])
    assert item["content"]["details"]["formula"] == ""
    response = semantic.client.post(f"/api/semantic/items/{item['id']}/review", json={
        "expected_revision": 1, "version": 1, "decision": "approve", "note": "검토"})
    assert response.status_code == 422
    assert "미정" in response.json()["detail"]
    result = lookup(semantic, "수율").json()
    assert result["resolution"] == "missing_knowledge"
    assert result["items"] == []


def test_approval_new_draft_keeps_old_published_version_and_conflict(semantic):
    item = approve(semantic, create(semantic, aliases=["FT"]))
    changed = {**item["content"], "description": "아직 승인되지 않은 새 설명"}
    body = {"expected_revision": item["revision"], "content": changed}
    saved = semantic.client.post(f"/api/semantic/items/{item['id']}/versions", json=body)
    assert saved.status_code == 201
    assert semantic.client.post(f"/api/semantic/items/{item['id']}/versions", json=body).status_code == 409
    result = lookup(semantic, "FT").json()
    assert result["items"][0]["version"] == 1
    assert result["items"][0]["content"]["description"] == item["content"]["description"]
    with semantic.sessions() as db:
        assert db.get(KnowledgeVersion, (item["id"], 1)).status == "approved"


def test_ambiguous_alias_is_reported_even_with_one_result_and_choice_resolves(semantic):
    first = approve(semantic, create(semantic, name="의미 A", aliases=["FT"]))
    approve(semantic, create(semantic, name="의미 B", aliases=["FT"]))
    result = lookup(semantic, "FT", max_items=1).json()
    assert result["resolution"] == "needs_clarification"
    assert len(result["clarifications"][0]["options"]) == 2
    selected = lookup(semantic, "FT", selected_definitions=[{"item_id": first["id"], "version": 1}]).json()
    assert selected["resolution"] == "resolved"
    assert [i["id"] for i in selected["items"]] == [first["id"]]
    assert lookup(semantic, "LEFT").json()["items"] == []


def test_metric_requires_approved_mapping_and_returns_output_rules(semantic):
    item = approve(semantic, create(semantic, kind="metric", name="검사 지표", details=metric_details()))
    assert lookup(semantic, "검사 지표").json()["resolution"] == "missing_knowledge"
    item = bind(semantic, item)
    result = lookup(semantic, "검사 지표").json()
    assert result["resolution"] == "resolved"
    assert result["output_constraints"][0]["aggregation"] == "test ratio of sums"
    assert result["items"][0]["binding"]["version"] == 1


def test_db_a_b_a_preserves_separate_mapping_versions(semantic):
    item = bind(semantic, approve(semantic, create(semantic)))
    first = lookup(semantic).json()
    semantic.monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "db-b", revision=2, ignored_tables=()))
    b_item = semantic.client.get("/api/semantic/items").json()["items"][0]
    assert b_item["binding"] is None
    bind(semantic, b_item, source="db-b-source")
    assert lookup(semantic).json()["items"][0]["binding"]["content"]["sources"][0]["source_id"] == "db-b-source"
    assert lookup(semantic, previous_context_id=first["context_id"]).status_code == 404
    semantic.monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "db-a", revision=3, ignored_tables=()))
    restored = lookup(semantic).json()
    assert restored["items"][0]["binding"]["content"]["sources"][0]["source_id"] == "db-a-source"
    assert semantic.client.post("/api/semantic/context", json={
        "query": "검사 용어", "connection_key": "db-a", "workspace_token": "db-a:1"}).status_code == 409


def test_removed_column_hidden_table_and_type_change_disable_without_deleting(semantic):
    item = bind(semantic, approve(semantic, create(semantic)))
    original = list(semantic.physical[0]["columns"])
    semantic.physical[0]["columns"] = [c for c in original if c["name"] != "status"]
    result = lookup(semantic).json()
    assert result["resolution"] == "missing_knowledge" and not result["items"]
    checked = semantic.client.post(f"/api/semantic/items/{item['id']}/validate", json={}).json()
    assert any("status" in issue for issue in checked["validation"]["binding_issues"])
    semantic.physical[0]["columns"] = original
    assert lookup(semantic).json()["resolution"] == "resolved"
    semantic.monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "db-a", revision=2, ignored_tables=(("public", "measurements"),)))
    assert lookup(semantic).json()["items"] == []
    semantic.monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "db-a", revision=3, ignored_tables=()))
    semantic.physical[0]["columns"] = [{"name": c["name"], "type": "integer" if c["name"] == "status" else c["type"]} for c in original]
    assert lookup(semantic).json()["items"] == []
    with semantic.sessions() as db:
        assert db.get(BindingVersion, (item["id"], 1, "db-a", 1)).status == "approved"


def test_permissions_no_drafts_or_unapproved_binding_in_reader_list(semantic):
    draft = create(semantic, name="비공개 초안")
    approved = approve(semantic, create(semantic))
    bind(semantic, approved, approve_mapping=False)
    semantic.actor.role = "viewer"
    assert semantic.client.get("/api/semantic/items").json()["items"] == []
    assert semantic.client.get(f"/api/semantic/items/{draft['id']}/versions").status_code == 403
    assert semantic.client.post("/api/semantic/items", json={"kind": "term", "content": {"name": "no"}}).status_code == 403
    assert lookup(semantic).json()["items"] == []


def test_followup_pins_definition_mapping_conditions_and_rejects_retired(semantic):
    item = bind(semantic, approve(semantic, create(semantic)))
    first = lookup(semantic, confirmed_conditions={"기간": "사용자가 지정한 기간"}).json()
    new = semantic.client.post(f"/api/semantic/items/{item['id']}/versions", json={
        "expected_revision": item["revision"], "content": {**item["content"], "description": "새 업무 설명"}}).json()
    new = bind(semantic, approve(semantic, new))
    second = lookup(semantic, "앞 조건을 유지해줘", previous_context_id=first["context_id"]).json()
    assert second["items"][0]["version"] == 1
    assert second["confirmed_conditions"]["기간"]["value"] == "사용자가 지정한 기간"
    retired = semantic.client.post(f"/api/semantic/items/{item['id']}/review", json={
        "expected_revision": new["revision"], "version": 1, "decision": "retire", "note": "잘못된 과거 정의"})
    assert retired.status_code == 200
    assert lookup(semantic, "계속", previous_context_id=first["context_id"]).json()["resolution"] == "missing_knowledge"
    assert semantic.client.get(f"/api/semantic/contexts/{first['context_id']}").status_code == 409
    semantic.actor.id = "other-user"
    assert semantic.client.get(f"/api/semantic/contexts/{first['context_id']}").status_code == 404


def test_dependencies_are_included_and_hidden_dependency_blocks_resolution(semantic):
    dep = bind(semantic, approve(semantic, create(semantic, name="원천 의미")))
    parent = approve(semantic, create(semantic, name="대표 용어", references=[{"item_id": dep["id"], "version": 1}]))
    result = lookup(semantic, "대표 용어").json()
    assert {i["id"] for i in result["items"]} == {parent["id"], dep["id"]}
    semantic.physical.clear()
    result = lookup(semantic, "대표 용어").json()
    assert result["resolution"] == "missing_knowledge"
    assert dep["id"] not in [i["id"] for i in result["items"]]


def test_sql_examples_are_statically_checked_never_executed(semantic):
    bad = create(semantic, kind="query", name="나쁜 질의", details={
        "question": "원천 변경", "sql": "DELETE FROM public.measurements", "assumptions": "test", "expected_result": "test"})
    response = semantic.client.post(f"/api/semantic/items/{bad['id']}/review", json={
        "expected_revision": 1, "version": 1, "decision": "approve", "note": "test"})
    assert response.status_code == 422
    good = approve(semantic, create(semantic, kind="query", name="검토 질의", details={
        "question": "목록", "sql": "SELECT status FROM public.measurements", "assumptions": "검토 전제", "expected_result": "담당자가 확인한 기대 결과"}))
    bind(semantic, good)
    assert lookup(semantic, "검토 질의").json()["items"][0]["content"]["details"]["sql"].startswith("SELECT")
    # No source engine exists in this fixture. Any attempted SQL execution would fail.
    assert database.current_source().engine is None


def test_typed_predicate_validation_and_cross_db_source_rejected(semantic):
    item = approve(semantic, create(semantic))
    item = bind(semantic, item, predicates=[{"source_id": "db-a-source", "column": "amount", "op": "eq", "value": "not a number"}], approve_mapping=False)
    response = semantic.client.post(f"/api/semantic/items/{item['id']}/bindings/review", json={
        "definition_version": 1, "expected_revision": 1, "version": 1, "decision": "approve", "note": "test"})
    assert response.status_code == 422
    assert "숫자형" in response.json()["detail"]
    assert lookup(semantic, schema_hint={"source_ids": ["db-b-source"]}).status_code == 422


def test_relation_is_advisory_and_accepts_composite_keys(semantic):
    item = approve(semantic, create(semantic, kind="relation", name="검사 연결", details={
        "cardinality": "many_to_many", "evidence": "담당자 확인", "duplication_notes": "사전 집계 필요", "join_notes": "두 컬럼으로 연결"}))
    result = semantic.client.post(f"/api/semantic/items/{item['id']}/bindings", json={
        "definition_version": 1, "expected_revision": 0,
        "content": {"sources": [{"source_id": "db-a-source", "role": "left", "columns": ["id", "status"]},
                                {"source_id": "db-a-source", "role": "right", "columns": ["id", "status"]}]} }).json()
    response = semantic.client.post(f"/api/semantic/items/{item['id']}/bindings/review", json={
        "definition_version": 1, "expected_revision": 1, "version": 1, "decision": "approve", "note": "test"})
    assert response.status_code == 200
    assert lookup(semantic, "검사 연결").json()["items"][0]["content"]["details"]["cardinality"] == "many_to_many"


def test_mapping_draft_does_not_replace_published_mapping_and_stale_save_rejects(semantic):
    item = bind(semantic, approve(semantic, create(semantic)))
    previous_revision = item["binding"]["revision"]
    new = bind(semantic, item, columns=["amount"], approve_mapping=False)
    assert new["binding"]["version"] == 2
    result = lookup(semantic).json()["items"][0]
    assert result["binding"]["version"] == 1
    assert result["binding"]["content"]["sources"][0]["columns"] == ["id", "status"]
    response = semantic.client.post(f"/api/semantic/items/{item['id']}/bindings", json={
        "definition_version": 1, "expected_revision": previous_revision, "content": {"sources": []}})
    assert response.status_code == 409


def test_query_dependency_detects_columns_not_explicitly_selected_in_mapping(semantic):
    item = approve(semantic, create(semantic, kind="query", name="상태 예제", details={
        "question": "상태", "sql": "SELECT status FROM public.measurements",
        "assumptions": "test assumptions", "expected_result": "test result"}))
    bind(semantic, item, columns=["id"])
    semantic.physical[0]["columns"] = [c for c in semantic.physical[0]["columns"] if c["name"] != "status"]
    assert lookup(semantic, "상태 예제").json()["resolution"] == "missing_knowledge"


def test_explicit_definitions_are_not_truncated_by_result_limit(semantic):
    a = approve(semantic, create(semantic, name="선택 A"))
    b = approve(semantic, create(semantic, name="선택 B"))
    result = lookup(semantic, "계속", max_items=1, selected_definitions=[
        {"item_id": a["id"], "version": 1}, {"item_id": b["id"], "version": 1}]).json()
    assert {i["id"] for i in result["items"]} == {a["id"], b["id"]}


def test_invalid_detail_update_is_422_and_other_db_items_are_not_visible(semantic):
    item = create(semantic, kind="data", name="행 설명", details={"grain": "test grain"})
    response = semantic.client.post(f"/api/semantic/items/{item['id']}/versions", json={
        "expected_revision": 1, "content": {**item["content"], "details": {"unknown_field": "bad"}}})
    assert response.status_code == 422
    semantic.monkeypatch.setattr(database, "active_source", database.SourceBinding(None, "db-b", revision=2, ignored_tables=()))
    assert semantic.client.get("/api/semantic/items").json()["items"] == []
    assert semantic.client.get(f"/api/semantic/items/{item['id']}/versions").status_code == 404
