"""Compare real question answers to independent SQL and exercise live jobs.

Local synthetic source only. Source is read-only; live runs use validation
purpose and cannot approve knowledge or populate the question inbox.
"""
import json
import math
from pathlib import Path
import sys
import time

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import db as database
from app.analytics import data
from app.database_settings import load_active_source
from benchmark_parameter_analytics import CASES, protected_hashes


def check_stats(actual, expected):
    assert actual["event_count"] == expected["n"]
    assert actual["valid_count"] == expected["valid"]
    assert actual["missing_count"] == expected["n"] - expected["valid"]
    assert math.isclose(actual["mean"], expected["mean"], rel_tol=1e-11)


def main():
    binding = load_active_source()
    assert binding.engine.url.host == "127.0.0.1" and binding.engine.url.database == "wafer_fab_demo"
    with database.SessionLocal() as db:
        snapshot, issues = data.source_snapshot(db)
    assert not issues
    before = json.loads((ROOT / ".local/analytics-benchmark-before.json").read_text(encoding="utf-8"))
    after = json.loads((ROOT / ".local/analytics-benchmark-after.json").read_text(encoding="utf-8"))
    assert before["protected_files"] == protected_hashes()
    assert all(before["cases"][k]["result"] == after["cases"][k]["result"] for k in CASES)
    chat = json.loads((ROOT / ".local/analysis-question-evaluation.json").read_text(encoding="utf-8"))
    report = {"all_results_unchanged": True, "protected_chat_files_unchanged": True, "checks": {}, "live_runs": []}
    with data.analysis_connection() as connection:
        assert connection.scalar(text("SHOW transaction_read_only")) == "on"
        epm = data.qualified(snapshot["epm"])
        history = data.qualified(snapshot["history"])
        expected = dict(connection.execute(text(f"SELECT count(*) n, count(value) valid, avg(value) mean FROM {epm} WHERE parameter='PARA1'")).mappings().one())
        case = next(c for c in chat["cases"] if c["name"] == "statistics")
        actual = next(e["data"]["query_result"]["rows"][0] for e in case["events"] if e["event"] == "proposal")
        assert actual["total_count"] == expected["n"] and actual["null_count"] == expected["n"]-expected["valid"]
        assert math.isclose(actual["avg_value"], expected["mean"], rel_tol=1e-11)
        report["checks"]["chat_statistics_matches_sql"] = actual
        trend = after["cases"]["single_trend"]["result"]["series"][0]
        for label, start, end in (("baseline", "2026-09-01T00:00:00Z", "2026-09-15T00:00:00Z"),
                                   ("current", "2026-09-15T00:00:00Z", "2026-10-01T00:00:00Z")):
            sql = f"""WITH chosen AS (
                SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y,parameter) value
                FROM {epm} WHERE parameter='PARA1' AND end_dt_tm>=:start AND end_dt_tm<:end
                ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,parameter,end_dt_tm DESC,record_id DESC)
                SELECT count(*) n,count(value) valid,avg(value) mean FROM chosen"""
            expected = connection.execute(text(sql), {"start": start, "end": end}).mappings().one()
            check_stats(trend[label], expected)
        assert trend["status"] == "needs_criteria"
        report["checks"]["trend_matches_independent_sql"] = {label: trend[label]["mean"] for label in ("baseline", "current")}
        report["checks"]["trend_matches_independent_sql"]["delta"] = trend["delta"]
        # This fixture has no revisits; assert that before using a simple SQL
        # oracle. Rework ambiguity is covered by isolated calculation tests.
        assert connection.scalar(text(f"SELECT count(*) FROM (SELECT 1 FROM {history} WHERE operation='oper A' "
            "GROUP BY lot_cd,lot_id,wf_id HAVING count(DISTINCT end_dt_tm)>1) repeated")) == 0
        sql = f"""WITH chosen AS (
            SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y,parameter) * FROM {epm}
            WHERE parameter='PARA1' AND end_dt_tm >= '2026-09-01T00:00:00Z' AND end_dt_tm < '2026-10-01T00:00:00Z'
            ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,parameter,end_dt_tm DESC,record_id DESC), matched AS (
            SELECT m.value,h.recipe FROM chosen m JOIN LATERAL (
                SELECT recipe FROM {history} h WHERE h.lot_cd=m.lot_cd AND h.lot_id=m.lot_id AND h.wf_id=m.wf_id
                AND h.operation='oper A' AND h.end_dt_tm<=m.end_dt_tm
                ORDER BY h.end_dt_tm DESC,h.record_id DESC LIMIT 1) h ON TRUE)
            SELECT recipe,count(*) n,count(value) valid,avg(value) mean FROM matched
            WHERE recipe IN ('RCP1','RCP2') GROUP BY recipe"""
        split = next(s for s in after["cases"]["split"]["result"]["series"] if s["family"] == "epm" and s["parameter"] == "PARA1")
        for expected in connection.execute(text(sql)).mappings():
            check_stats(split["group_a" if expected["recipe"] == "RCP1" else "group_b"], expected)
        assert split["status"] == "needs_criteria"
        report["checks"]["split_matches_independent_sql"] = {"RCP1": split["group_a"]["mean"], "RCP2": split["group_b"]["mean"], "delta": split["delta"]}
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=30) as client:
        for name in ("statistics", "single_trend", "split"):
            started = time.perf_counter()
            response = client.post("/api/analytics/runs", json={"purpose": "validation", **CASES[name]})
            response.raise_for_status()
            run = response.json()
            while run["status"] in {"queued", "running"}:
                assert time.perf_counter()-started < 60, "Live job exceeded the verification deadline."
                time.sleep(0.1)
                response = client.get("/api/analytics/runs/" + run["id"])
                response.raise_for_status()
                run = response.json()
            assert run["status"] == "completed", run.get("error")
            assert not run["questions"]
            assert run["result"]["series"] == after["cases"][name]["result"]["series"]
            from datetime import datetime
            timing = {"case": name, "run_id": run["id"], "response_seconds": round(time.perf_counter()-started, 3),
                      "queue_seconds": round((datetime.fromisoformat(run["started_at"])-datetime.fromisoformat(run["created_at"])).total_seconds(),3),
                      "compute_job_seconds": round((datetime.fromisoformat(run["completed_at"])-datetime.fromisoformat(run["started_at"])).total_seconds(),3)}
            report["live_runs"].append(timing)
            print(json.dumps(timing), flush=True)
    (ROOT / ".local/analysis-answer-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["checks"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
