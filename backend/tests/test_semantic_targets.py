from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text

from app.semantic.collector_targets import infer_targets, question_presentation, validated_targets
from app.semantic.migration import migrate_semantic
from app.semantic.collector_service import question_fingerprint


@pytest.fixture
def dataset():
    return {"id": "results", "name": "검사 결과", "version": 1, "project_id": None,
            "columns": [{"name": name} for name in ("passed", "status", "quality")],
            "sources": [{"id": "source", "schema_name": "public", "table_name": "die_results", "columns": {"passed": "boolean", "status": "text"}}],
            "spec": {"source_id": "source", "raw_sql": "SELECT passed, status, CASE WHEN passed THEN 'PASS' ELSE 'FAIL' END AS quality FROM die_results"}}


def test_legacy_question_has_dataset_context_and_schema_confirmed_column(dataset):
    q = SimpleNamespace(dataset=dataset, title="die_results.passed는 무엇인가요?", reason="판정 의미 확인", targets=[], topic="column_meaning", subject="passed")
    shown = question_presentation(q)
    assert shown["title"] == "passed는 무엇인가요?"
    assert shown["targets"] == [{"kind": "column", "column": "passed", "source_id": "", "value": "", "source_name": ""}]
    assert q.title == "die_results.passed는 무엇인가요?"  # Original history is unchanged.


def test_values_are_explicit_and_cannot_be_invented_or_taken_from_other_columns(dataset):
    target = {"kind": "column_value", "column": "quality", "value": "PASS"}
    result = validated_targets(dataset, [target], generated=True)
    assert result[0]["kind"] == "column_value" and result[0]["value"] == "PASS"
    for invalid in [{**target, "value": "UNKNOWN"}, {**target, "column": "status"}, {"kind": "column", "column": "invented"}]:
        with pytest.raises(ValueError):
            validated_targets(dataset, [invalid], generated=True)
    # An explicit administrator input may describe an observed value absent from SQL.
    assert validated_targets(dataset, [{**target, "value": "KNOWN-BY-USER"}], generated=False)


def test_literals_are_not_misclassified_or_rewritten_as_identifiers(dataset):
    q = SimpleNamespace(dataset=dataset, title="status 컬럼의 'die_results.passed' 값은 무엇인가요?", reason="의미 확인",
                        targets=[{"kind": "column_value", "column": "status", "source_id": "", "value": "die_results.passed"}], topic="other", subject="status")
    assert question_presentation(q)["title"] == q.title
    assert [t.column for t in infer_targets(dataset, "'passed' 값은 무엇인가요?")] == []
    assert [t.column for t in infer_targets(dataset, "passed 컬럼은 무엇인가요?")] == ["passed"]
    assert infer_targets(dataset, "미정 업무 용어란 무엇인가요?") == []


def test_column_value_identity_distinguishes_values_without_changing_column_identity(dataset):
    first = validated_targets(dataset, [{"kind": "column_value", "column": "quality", "value": "PASS"}])
    second = validated_targets(dataset, [{"kind": "column_value", "column": "quality", "value": "FAIL"}])
    assert question_fingerprint("db", dataset, "column_meaning", "quality", first) != question_fingerprint("db", dataset, "column_meaning", "quality", second)


def test_migration_adds_targets_to_existing_questions_without_erasing_history():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE app_semantic_questions (id VARCHAR(36) PRIMARY KEY, title TEXT)"))
        conn.execute(text("INSERT INTO app_semantic_questions (id, title) VALUES ('old', '기존 질문')"))
        conn.execute(text("CREATE TABLE app_semantic_answers (id VARCHAR(36) PRIMARY KEY, text TEXT)"))
        conn.execute(text("INSERT INTO app_semantic_answers (id, text) VALUES ('old-answer', '기존 답변 원문')"))
    migrate_semantic(engine)
    migrate_semantic(engine)
    with engine.connect() as conn:
        assert "targets" in {c["name"] for c in inspect(conn).get_columns("app_semantic_questions")}
        assert conn.execute(text("SELECT title, targets FROM app_semantic_questions WHERE id='old'")).one() == ("기존 질문", "[]")
        assert conn.execute(text("SELECT text, targets FROM app_semantic_answers WHERE id='old-answer'")).one() == ("기존 답변 원문", "[]")
        assert conn.scalar(text("SELECT count(*) FROM app_semantic_migrations WHERE version=4")) == 1
    engine.dispose()
