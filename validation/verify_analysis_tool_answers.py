"""Read-only SQL oracle for actual model-selected tool runs and continuation."""
import json
from pathlib import Path
import sys

import httpx
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import db as database
from app.analytics import data
from app.database_settings import load_active_source
from verify_analysis_answers import check_stats


def main():
    binding = load_active_source()
    assert binding.engine.url.host == "127.0.0.1" and binding.engine.url.database == "wafer_fab_demo"
    with database.SessionLocal() as db:
        snapshot, issues = data.source_snapshot(db)
    assert not issues
    report = json.loads((ROOT / ".local/analysis-tools-evaluation.json").read_text(encoding="utf-8"))
    cases = {c["name"]: c for c in report["cases"]}
    answers = {name: next(e["data"] for e in c["events"] if e["event"] == "analysis") for name, c in cases.items()}
    checks = {}
    with data.analysis_connection() as connection:
        assert connection.scalar(text("SHOW transaction_read_only")) == "on"
        epm, history = data.qualified(snapshot["epm"]), data.qualified(snapshot["history"])
        expected = connection.execute(text(f"SELECT count(*) n,count(value) valid,avg(value) mean FROM {epm} WHERE parameter='PARA1'")).mappings().one()
        check_stats(answers["statistics"]["series"][0]["statistics"], expected)
        checks["statistics_counts_mean"] = True
        trend_sql = f"""WITH chosen AS (
            SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y,parameter) value
            FROM {epm} WHERE parameter=:parameter AND end_dt_tm>=:start AND end_dt_tm<:end
            ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,parameter,end_dt_tm DESC,record_id DESC)
            SELECT count(*) n,count(value) valid,avg(value) mean FROM chosen"""
        def verify_trend(answer, parameter):
            q, s = answer["request"], answer["series"][0]
            for label in ("baseline", "current"):
                expected = connection.execute(text(trend_sql), {"parameter": parameter, "start": q[label+"_start"], "end": q[label+"_end"]}).mappings().one()
                check_stats(s[label], expected)
            assert abs(s["delta"] - (s["current"]["mean"] - s["baseline"]["mean"])) < 1e-12
            assert s["status"] == "needs_criteria"
        verify_trend(answers["explicit_trend"], "PARA1")
        checks["trend_counts_mean_delta"] = True
        assert connection.scalar(text(f"SELECT count(*) FROM (SELECT 1 FROM {history} WHERE operation='oper A' GROUP BY lot_cd,lot_id,wf_id HAVING count(DISTINCT end_dt_tm)>1) repeated")) == 0
        split_sql = f"""WITH chosen AS (
            SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y,parameter) * FROM {epm}
            WHERE parameter='PARA1' AND end_dt_tm>='2026-09-01T00:00:00Z' AND end_dt_tm<'2026-10-01T00:00:00Z'
            ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,parameter,end_dt_tm DESC,record_id DESC), matched AS (
            SELECT m.value,h.recipe FROM chosen m JOIN LATERAL (
                SELECT recipe FROM {history} h WHERE h.lot_cd=m.lot_cd AND h.lot_id=m.lot_id AND h.wf_id=m.wf_id
                AND h.operation='oper A' AND h.end_dt_tm<=m.end_dt_tm ORDER BY h.end_dt_tm DESC,h.record_id DESC LIMIT 1) h ON TRUE)
            SELECT recipe,count(*) n,count(value) valid,avg(value) mean FROM matched WHERE recipe IN ('RCP1','RCP2') GROUP BY recipe"""
        split = answers["recipe_split"]["series"][0]
        for expected in connection.execute(text(split_sql)).mappings():
            check_stats(split["group_a" if expected["recipe"] == "RCP1" else "group_b"], expected)
        assert abs(split["delta"] - (split["group_b"]["mean"] - split["group_a"]["mean"])) < 1e-12
        assert split["status"] == "needs_criteria"
        checks["split_counts_mean_delta"] = True
        conversation_id = next(e["data"]["id"] for e in cases["explicit_trend"]["events"] if e["event"] == "conversation")
        with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False) as client:
            saved = client.get("/api/chat/conversations/" + conversation_id).json()
        assert saved["revision"] == 2
        followup = saved["messages"][-1]["analysis"]
        assert followup["request"]["parameter"] == "PARA2"
        for key in ("baseline_start", "baseline_end", "current_start", "current_end", "retest_policy"):
            assert followup["request"][key] == answers["explicit_trend"]["request"][key]
        verify_trend(followup, "PARA2")
        checks["browser_followup_preserves_conditions_and_matches_sql"] = True
    (ROOT / ".local/analysis-tools-sql-verification.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps(checks))


if __name__ == "__main__":
    main()
