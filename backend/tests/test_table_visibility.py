from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db import Settings, settings
from app.table_visibility import TableVisibility, table_ignored


def test_json_list_setting_and_normalization(monkeypatch):
    with monkeypatch.context() as patched:
        patched.setenv("TABLE_IGNORE", '[" public.audit_log ","scratch","public.audit_log"]')
        assert Settings(_env_file=None).table_ignore == ["public.audit_log", "scratch"]
    monkeypatch.delenv("TABLE_IGNORE", raising=False)
    assert Settings(_env_file=None).table_ignore == []


@pytest.mark.parametrize("value", ["audit_log", [""], ["public."], ["public.*"], ["a.b.c"], [1]])
def test_invalid_ignore_settings_fail_at_startup(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, table_ignore=value)


def test_schema_specific_bare_name_and_case_matching(monkeypatch):
    monkeypatch.setattr(settings, "table_ignore", ["public.audit_log", "scratch", "public.MixedCase"])
    assert table_ignored("public", "audit_log")
    assert not table_ignored("private", "audit_log")
    assert table_ignored("public", "scratch") and table_ignored("private", "scratch")
    assert table_ignored("public", "MixedCase") and not table_ignored("public", "mixedcase")
    assert not table_ignored("public", "audit_log_archive")


def test_saved_sql_and_join_dependencies_use_physical_table_names(monkeypatch):
    monkeypatch.setattr(settings, "table_ignore", ["public.equipment"])
    sources = [SimpleNamespace(id=id, schema_name=schema, table_name=table) for id, schema, table in [
        ("facts", "public", "facts"), ("alias_for_equipment", "public", "equipment"),
        ("other_equipment", "other", "equipment"),
    ]]
    relation = SimpleNamespace(id="facts_equipment", left_source_id="facts", right_source_id="alias_for_equipment")
    policy = TableVisibility(sources, [relation])
    assert not policy.allows_spec({"source_id": "alias_for_equipment"})
    assert policy.allows_spec({"source_id": "other_equipment"})
    assert not policy.allows_spec({"source_id": "facts", "joins": ["facts_equipment"]})
    assert not policy.allows_spec({"source_id": "facts", "raw_sql":
        "WITH e AS (SELECT * FROM public.equipment) SELECT * FROM e"})
    assert not policy.allows_spec({"source_id": "facts", "raw_sql":
        "SELECT f.id FROM facts f JOIN public.equipment e ON f.id=e.id"})
    assert policy.allows_spec({"source_id": "facts", "raw_sql":
        "WITH equipment AS (SELECT * FROM facts) SELECT * FROM equipment"})
    assert policy.allows_spec({"source_id": "facts", "raw_sql":
        "SELECT 'public.equipment' AS label FROM facts"})


def test_empty_list_preserves_saved_definitions():
    assert TableVisibility().allows_spec({"source_id": "facts", "raw_sql": "SELECT id FROM facts"})
