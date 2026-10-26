"""Read-only verification of SQL captured from two real app AI conversations.

Run from the repository root with PYTHONPATH=backend. Requires the mounted demo
DB and .local/ai-pivot-{single,join}.sql, captured without modifying the AI SQL.
No LLM calls, dataset saves, source writes or schema changes are performed here.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import sqlglot
from sqlglot import exp

from app.database_settings import load_active_source
from app.db import use_source
from app.query import clean, source_connection


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
KEYS = {
    "single": ["lot_code", "wafer_id", "site_x", "site_y"],
    "join": ["lot_code", "wafer_code", "wafer_number", "site_x", "site_y"],
}


def api(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Workspace-Token"] = token
    request = Request("http://127.0.0.1:8000/api" + path,
                      data=None if body is None else json.dumps(body).encode(),
                      headers=headers)
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def oracle(measurements, wafers, mode):
    """Select last records in Python; no SQL windows/aggregates are reused."""
    wafer_map = {w["id"]: w for w in wafers}
    latest = {}
    for measurement in measurements:
        row = dict(measurement)
        if mode == "single":
            if row["wafer_id"] not in (1, 2):
                continue
        else:
            wafer = wafer_map.get(row["wafer_id"])
            if not wafer or wafer["project_id"] != 1 or wafer["wafer_number"] not in (1, 2):
                continue
            for key in ("lot_code", "wafer_code", "wafer_number"):
                row[key] = wafer[key]
        keys = tuple(row[key] for key in KEYS[mode])
        metric = row["metric_name"]
        if any(key is None for key in keys) or row["measured_at"] is None:
            continue
        if metric not in ("CD", "resistance"):
            continue
        group = (*keys, metric)
        rank = row["measured_at"], row["id"]
        if group not in latest or rank > latest[group][0]:
            latest[group] = rank, row["value"]
    result = []
    for keys in sorted({group[:-1] for group in latest}):
        values = [latest.get((*keys, metric), (None, None))[1] for metric in ("CD", "resistance")]
        if all(value is not None for value in values):
            result.append(dict(zip([*KEYS[mode], "value_cd", "value_resistance"], [*keys, *values])))
    return result


def assert_rows(actual, expected, label):
    assert len(actual) == len(expected), (label, "row count", len(actual), len(expected))
    for index, (got, want) in enumerate(zip(actual, expected)):
        assert set(got) == set(want), (label, index, "columns")
        for key, value in want.items():
            if isinstance(value, float):
                assert got[key] is not None and math.isclose(got[key], value, rel_tol=1e-12, abs_tol=1e-12), (label, index, key)
            else:
                assert got[key] == value, (label, index, key, got[key], value)


def fixtures():
    measurements = []

    def add(wafer, x, metric, value, hour, *, identity=None, lot="M-LOT", y=0):
        measurements.append({
            "id": identity or 100 + len(measurements), "wafer_id": wafer,
            "project_id": 1, "lot_code": lot, "wafer_code": f"M-W{wafer}",
            "site_x": x, "site_y": y, "metric_name": metric, "value": value,
            "measured_at": None if hour is None else datetime(2026, 10, 1, hour, tzinfo=timezone.utc),
        })

    # Independent measurement times, last versus maximum, tie-breaking and NULL time.
    add(1, 0, "CD", 999, 8, identity=900)
    add(1, 0, "CD", 12, 10)
    add(1, 0, "CD", 13, 10)
    add(1, 0, "resistance", 999, 7)
    add(1, 0, "resistance", 20, 11)
    add(1, 0, "CD", 555, None)
    add(1, 0, "overlay", 9999, 12)
    # Missing operation and latest NULL must both exclude the final pair.
    add(1, 1, "CD", 30, 10)
    add(1, 2, "CD", 40, 8)
    add(1, 2, "CD", None, 10)
    add(1, 2, "resistance", 50, 11)
    # Zero/negative values remain valid and wafer keys must stay separate.
    add(1, 3, "CD", 0, 10)
    add(1, 3, "resistance", -2, 9)
    add(2, 0, "CD", 60, 9)
    add(2, 0, "resistance", 70, 10)
    add(1, None, "CD", 1, 10)
    add(1, None, "resistance", 2, 11)
    # A NULL m.lot_code must be excluded in single mode, but joined w.lot_code
    # remains usable: this verifies the AI selected columns from the right table.
    add(1, 4, "CD", 80, 10, lot=None)
    add(1, 4, "resistance", 90, 11, lot=None)
    # JOIN must not accidentally carry the previous wafer_id 1,2 restriction.
    # Also cover project/wafer_number filters, unmatched JOIN and NULL join key.
    for wafer in (76, 3, 4, 5, 99, None):
        add(wafer, 5, "CD", 100, 10)
        add(wafer, 5, "resistance", 200, 11)
    wafers = [
        {"id": 1, "project_id": 1, "wafer_number": 1, "lot_code": "W-LOT-1", "wafer_code": "W1"},
        {"id": 2, "project_id": 1, "wafer_number": 2, "lot_code": "W-LOT-1", "wafer_code": "W2"},
        {"id": 76, "project_id": 1, "wafer_number": 1, "lot_code": "W-LOT-2", "wafer_code": "W76"},
        {"id": 3, "project_id": 2, "wafer_number": 1, "lot_code": "OTHER", "wafer_code": "W3"},
        {"id": 4, "project_id": 1, "wafer_number": 3, "lot_code": "OTHER", "wafer_code": "W4"},
        {"id": 5, "project_id": 1, "wafer_number": 1, "lot_code": None, "wafer_code": "W5"},
    ]
    return measurements, wafers


def fixture_sql(sql, schemas, fixture_tables):
    """Replace physical inputs with typed VALUES CTEs, retaining AI operations."""
    tree = sqlglot.parse_one(sql, read="postgres")
    used = set()
    for table in tree.find_all(exp.Table):
        name = table.name
        if table.db == "public" and name in fixture_tables:
            used.add(name)
            if not table.alias:
                table.set("alias", exp.TableAlias(this=exp.to_identifier(name)))
            table.set("this", exp.to_identifier("__qa_" + name))
            table.set("db", None)
            table.set("catalog", None)
    assert used == set(fixture_tables), used
    ctes = []
    for name, rows in fixture_tables.items():
        columns = schemas[name]
        tuples = []
        for row in rows:
            literals = []
            for column in columns:
                value = row.get(column["name"])
                if isinstance(value, datetime):
                    value = value.isoformat()
                literal = exp.convert(value)
                literals.append(exp.Cast(this=literal, to=exp.DataType.build(column["type"], dialect="postgres")))
            tuples.append(exp.Tuple(expressions=literals))
        ctes.append(exp.CTE(
            this=exp.Values(expressions=tuples),
            alias=exp.TableAlias(this=exp.to_identifier("__qa_" + name),
                                columns=[exp.to_identifier(c["name"]) for c in columns]),
        ))
    existing = tree.args.get("with_")
    if existing:
        existing.set("expressions", [*ctes, *existing.expressions])
    else:
        tree.set("with_", exp.With(expressions=ctes))
    return tree.sql(dialect="postgres")


def main():
    bootstrap = api("/bootstrap")
    schemas = {source["table_name"]: source["columns"] for source in bootstrap["catalog"]}
    measurements, wafers = fixtures()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "scenarios": {}}
    with use_source(load_active_source()):
        with source_connection() as connection:
            report["source_transaction_read_only"] = connection.exec_driver_sql("SHOW transaction_read_only").scalar()
            assert report["source_transaction_read_only"] == "on"
            real_wafers = [dict(row) for row in connection.exec_driver_sql(
                "SELECT * FROM public.wafers WHERE project_id = 1 AND wafer_number IN (1, 2)"
            ).mappings()]
            # Fetch the union of the two scenarios' input IDs, without joining or
            # calculating last values in SQL; the independent oracle does that.
            from sqlalchemy import bindparam, text
            statement = text("SELECT * FROM public.inline_measurements WHERE wafer_id IN :ids").bindparams(bindparam("ids", expanding=True))
            real_measurements = [dict(row) for row in connection.execute(
                statement, {"ids": sorted({1, 2, *(w["id"] for w in real_wafers)})}
            ).mappings()]
            for mode in ("single", "join"):
                sql = (LOCAL / f"ai-pivot-{mode}.sql").read_text(encoding="utf-8")
                preview = api("/datasets/preview", {
                    "spec": {"source_id": "inline_measurements", "raw_sql": sql},
                    "editor_mode": "sql", "context": {"limit": 100},
                }, bootstrap["workspace_token"])
                expected = oracle(real_measurements, real_wafers, mode)
                assert expected, "Demo scenario must have results"
                assert len(expected) < 100, "Increase preview limit to avoid testing a truncated result"
                assert preview["canonical_sql"] == sql
                assert preview["sql"] == sql
                assert [c["name"] for c in preview["columns"]] == [*KEYS[mode], "value_cd", "value_resistance"]
                assert_rows(preview["rows"], expected, mode + " real data")
                fixture_tables = {"inline_measurements": measurements}
                if mode == "join":
                    fixture_tables["wafers"] = wafers
                synthetic_sql = fixture_sql(sql, schemas, fixture_tables)
                (LOCAL / f"ai-pivot-{mode}-fixture.sql").write_text(synthetic_sql, encoding="utf-8")
                fixture_actual = [dict(row) for row in connection.exec_driver_sql(synthetic_sql).mappings()]
                fixture_expected = oracle(measurements, wafers, mode)
                assert len(fixture_expected) == (3 if mode == "single" else 5)
                assert_rows(fixture_actual, fixture_expected, mode + " edge cases")
                report["scenarios"][mode] = {
                    "real_rows": len(expected), "real_results_match": True,
                    "fixture_rows": len(fixture_actual), "fixture_results_match": True,
                    "sql_text_unchanged": True, "simple_editor": preview["simple_editor"],
                    "fixture_results": clean(fixture_actual),
                }
                (LOCAL / f"ai-pivot-{mode}-preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    after_bootstrap = api("/bootstrap")
    after = {"datasets": after_bootstrap["datasets"], "dashboard": api("/dashboards")}
    before = json.loads((LOCAL / "ai-pivot-before.json").read_text(encoding="utf-8"))
    assert before == after, "Dataset/dashboard configuration changed during verification"
    report["existing_configuration_unchanged"] = True
    report["datasets"] = len(after["datasets"])
    report["cards"] = len(after["dashboard"]["cards"])
    report["dashboard_version"] = after["dashboard"]["version"]
    (LOCAL / "ai-pivot-after.json").write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    (LOCAL / "ai-pivot-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
