"""Read-only, repeatable timing and numerical regression on the local demo DB."""
import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import db as database
from app.database_settings import load_active_source
from app.analytics import data
from app.analytics.schemas import AnalysisRequest
from app.analytics.service import compute_request

PERIODS = dict(baseline_start="2026-09-01T00:00:00Z", baseline_end="2026-09-15T00:00:00Z",
               current_start="2026-09-15T00:00:00Z", current_end="2026-10-01T00:00:00Z")
CASES = {
    "statistics": dict(kind="statistics"),
    "all_changes": dict(kind="trend", **PERIODS, retest_policy="latest_per_site"),
    "single_trend": dict(kind="trend", family="epm", parameter="PARA1", **PERIODS, retest_policy="latest_per_site"),
    "split": dict(kind="split", operation="oper A", recipe_a="RCP1", recipe_b="RCP2",
                  start="2026-09-01T00:00:00Z", end="2026-10-01T00:00:00Z", retest_policy="latest_per_site"),
}


def protected_hashes():
    files = [ROOT / "backend/app/assistant.py", ROOT / "backend/app/chat_tools.py",
             ROOT / "backend/app/chat_workflow.py", ROOT / "backend/app/chat_contracts.py",
             ROOT / "frontend/src/ChatDrawer.tsx", *sorted((ROOT / "backend/app/chat_skills").glob("*.md")),
             ROOT / "backend/app/chat_skills/__init__.py"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--case", choices=list(CASES))
    args = parser.parse_args()
    binding = load_active_source()
    assert binding.engine.url.host == "127.0.0.1" and binding.engine.url.database == "wafer_fab_demo"
    with database.SessionLocal() as db:
        snapshot, issues = data.source_snapshot(db)
    assert not issues
    report = {"protected_files": protected_hashes(), "cases": {}}
    for name, body in CASES.items():
        if args.case and name != args.case:
            continue
        request = AnalysisRequest(purpose="validation", **body)
        selected = data.selected_snapshot(snapshot, request)
        profiler = cProfile.Profile()
        started = time.perf_counter()
        if args.profile:
            profiler.enable()
        rows, histories, watermark = data.extract(selected, request)
        extracted = time.perf_counter()
        result = compute_request(request, rows, histories)
        finished = time.perf_counter()
        if args.profile:
            profiler.disable()
            with (ROOT / ".local" / f"analytics-profile-{name}-{args.phase}.txt").open("w", encoding="utf-8") as stream:
                pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(35)
        timing = {"extract_seconds": round(extracted-started, 3), "compute_seconds": round(finished-extracted, 3),
                  "total_seconds": round(finished-started, 3), "rows": len(rows), "series": len(result["series"])}
        print(json.dumps({"case": name, **timing}), flush=True)
        report["cases"][name] = {**timing, "result": result}
        del rows, histories
    (ROOT / ".local" / f"analytics-benchmark-{args.phase}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
