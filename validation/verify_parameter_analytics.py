"""Validate the analytics adapter against the selected local synthetic fab DB.

Writes clearly marked analysis runs to metadata. Never writes source rows or
approves knowledge. Validation windows are fixture-derived test inputs only.
"""
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select, text
from app import db as database
from app.auth import LOCAL_ADMIN_USERNAME
from app.database_settings import load_active_source
from app.models import User
from app.semantic.migration import migrate_semantic
from app.semantic.models import KnowledgeItem
from app.analytics import data
from app.analytics.models import AnalysisRun, migrate_analytics
from app.analytics.schemas import AnalysisRequest
from app.analytics.service import create_run
from app.analytics.worker import tick


def main():
    binding = load_active_source()
    url = binding.engine.url
    if url.host != "127.0.0.1" or url.database != "wafer_fab_demo":
        raise RuntimeError("This check requires the selected local synthetic wafer_fab_demo.")
    migrate_semantic(database.engine)
    migrate_analytics(database.engine)
    with database.SessionLocal() as db:
        actor = db.scalar(select(User).where(User.username == LOCAL_ADMIN_USERNAME, User.active.is_(True)))
        assert actor and actor.role == "admin"
        before_knowledge = db.scalar(select(func.count()).select_from(KnowledgeItem))
        catalog = data.catalog(db)
        snapshot, issues = data.source_snapshot(db)
    assert not issues and len(catalog["series"]) == 17
    report = {"source": "local synthetic wafer_fab_demo", "checks": {}, "runs": []}

    def execute(body):
        request = AnalysisRequest.model_validate({"purpose": "validation", **body})
        start = time.perf_counter()
        with database.SessionLocal.begin() as db:
            run = create_run(db, request, actor)
            identity = run.id
        tick()
        with database.SessionLocal() as db:
            run = db.get(AnalysisRun, identity)
            assert run.status == "completed", (run.status, run.error)
            assert not run.question_ids, "Technical verification must not pollute the knowledge inbox."
            report["runs"].append({"id": identity, "kind": run.kind, "seconds": round(time.perf_counter()-start, 2),
                                   "series": len(run.result["series"]), "rows": run.result.get("row_count", run.result.get("raw_row_count")),
                                   "status": run.status})
            return run.result

    statistics = execute({"kind": "statistics"})
    expected_count = 0
    with data.analysis_connection() as connection:
        assert connection.scalar(text("SHOW transaction_read_only")) == "on"
        for family in ("inline", "epm"):
            op = "operation" if family == "inline" else "''"
            rows = connection.execute(text(f"SELECT {op} AS operation, parameter, count(*) AS n, count(value) AS valid, "
                f"avg(value) AS mean FROM {data.qualified(snapshot[family])} GROUP BY operation, parameter" if family == "inline" else
                f"SELECT '' AS operation, parameter, count(*) AS n, count(value) AS valid, avg(value) AS mean "
                f"FROM {data.qualified(snapshot[family])} GROUP BY parameter")).mappings()
            for row in rows:
                actual = next(s["statistics"] for s in statistics["series"] if s["family"] == family and
                              s["operation"] == row["operation"] and s["parameter"] == row["parameter"])
                assert actual["event_count"] == row["n"] and actual["valid_count"] == row["valid"]
                assert math.isclose(actual["mean"], row["mean"], rel_tol=1e-11)
                expected_count += row["n"]
    assert statistics["row_count"] == expected_count == 710162
    report["checks"]["full_statistics_match_independent_sql"] = True
    report["checks"]["source_read_only"] = True

    begin = datetime.fromisoformat(catalog["extent"]["start"])
    end = datetime.fromisoformat(catalog["extent"]["end"]) + timedelta(microseconds=1)
    middle = begin + (end-begin)/2
    periods = dict(baseline_start=begin, baseline_end=middle, current_start=middle, current_end=end)
    changes = execute({"kind": "trend", "retest_policy": "latest_per_site", **periods})
    assert len(changes["series"]) == 17 and all(s["status"] == "needs_criteria" for s in changes["series"])
    trend = execute({"kind": "trend", "family": "epm", "parameter": "PARA1", "retest_policy": "latest_per_site", **periods})
    assert len(trend["series"]) == 1 and trend["series"][0]["points"]
    with data.analysis_connection() as connection:
        table = data.qualified(snapshot["epm"])
        for label, start, finish in (("baseline", begin, middle), ("current", middle, end)):
            sql = f"""WITH chosen AS (
                SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y,parameter) value
                FROM {table} WHERE parameter=:parameter AND end_dt_tm>=:start AND end_dt_tm<:end
                ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,parameter,end_dt_tm DESC,record_id DESC)
                SELECT count(*) AS n,count(value) AS valid,avg(value) AS mean FROM chosen"""
            expected = connection.execute(text(sql), {"parameter": "PARA1", "start": start, "end": finish}).mappings().one()
            actual = trend["series"][0][label]
            assert actual["event_count"] == expected["n"] and actual["valid_count"] == expected["valid"]
            assert math.isclose(actual["mean"], expected["mean"], rel_tol=1e-11)
    report["checks"]["trend_latest_null_and_window_match_sql"] = True
    report["checks"]["no_implicit_change_threshold"] = True

    split = execute({"kind": "split", "operation": "oper A", "recipe_a": "RCP1", "recipe_b": "RCP2",
                     "start": begin, "end": end, "retest_policy": "latest_per_site"})
    assert split["matched_count"] > 0 and split["excluded_ambiguous_wafer_count"] == 0
    with data.analysis_connection() as connection:
        for family in ("inline", "epm"):
            table = data.qualified(snapshot[family])
            history = data.qualified(snapshot["history"])
            op = "operation" if family == "inline" else "'' AS operation"
            key = "lot_cd,lot_id,wf_id,die_x,die_y," + ("operation," if family == "inline" else "") + "parameter"
            sql = f"""WITH chosen AS (
                SELECT DISTINCT ON ({key}) lot_cd,lot_id,wf_id,die_x,die_y,{op},parameter,value,end_dt_tm
                FROM {table} WHERE end_dt_tm>=:start AND end_dt_tm<:end
                ORDER BY {key},end_dt_tm DESC,record_id DESC), matched AS (
                SELECT m.*,h.recipe FROM chosen m JOIN LATERAL (
                    SELECT recipe FROM {history} h WHERE h.lot_cd=m.lot_cd AND h.lot_id=m.lot_id AND h.wf_id=m.wf_id
                    AND h.operation=:operation AND h.end_dt_tm<=m.end_dt_tm
                    ORDER BY h.end_dt_tm DESC,h.record_id DESC LIMIT 1) h ON TRUE)
                SELECT operation,parameter,recipe,count(*) AS n,count(value) AS valid,avg(value) AS mean
                FROM matched WHERE recipe IN ('RCP1','RCP2') GROUP BY operation,parameter,recipe"""
            for expected in connection.execute(text(sql), {"start": begin, "end": end, "operation": "oper A"}).mappings():
                series = next(s for s in split["series"] if s["family"]==family and s["operation"]==expected["operation"] and s["parameter"]==expected["parameter"])
                actual = series["group_a" if expected["recipe"]=="RCP1" else "group_b"]
                assert actual["event_count"] == expected["n"] and actual["valid_count"] == expected["valid"]
                assert math.isclose(actual["mean"], expected["mean"], rel_tol=1e-11)
    report["checks"]["split_asof_cohorts_match_independent_sql"] = True
    report["split"] = {k: split[k] for k in ("matched_count", "unmatched_count", "cohorts", "excluded_ambiguous_wafer_count")}

    # A real unresolved request intentionally leaves actionable questions for the user.
    with database.SessionLocal.begin() as db:
        pending = create_run(db, AnalysisRequest(kind="trend"), actor)
        assert pending.status == "needs_input" and len(pending.question_ids) == 3
        report["inbox_request"] = {"id": pending.id, "questions": len(pending.question_ids)}
        assert db.scalar(select(func.count()).select_from(KnowledgeItem)) == before_knowledge
    report["checks"]["missing_criteria_reach_inbox_without_approved_knowledge_writes"] = True
    destination = ROOT / ".local" / "analytics-validation.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
