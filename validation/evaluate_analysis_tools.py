"""Authorized real-model evaluation; never logs credentials or applies proposals."""
import json
import os
from pathlib import Path
import time

import httpx
from evaluate_analysis_questions import QUESTIONS

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / ".local" / "analysis-tools-evaluation.json"
    selected = os.environ.get("ANALYTICS_EVAL_CASE")
    report = json.loads(destination.read_text(encoding="utf-8")) if selected and destination.exists() else {"cases": []}
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=180) as client:
        assert client.get("/api/auth/session").json()["status"] == "authenticated"
        assert client.get("/api/llm/status").json()["configured"]
        for name, question in QUESTIONS:
            if selected and selected != name:
                continue
            case = {"name": name, "question": question, "events": []}
            start = time.perf_counter()
            with client.stream("POST", "/api/chat", json={"message": question, "save_history": True}) as response:
                response.raise_for_status()
                event = None
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        value = json.loads(line[6:])
                        case["events"].append({"event": event, "seconds": round(time.perf_counter() - start, 3), "data": value})
            case["seconds"] = round(time.perf_counter() - start, 3)
            answer = next((e["data"] for e in case["events"] if e["event"] == "analysis"), None)
            case["checks"] = {"analysis_tool_used": answer is not None, "no_error": not any(e["event"] == "error" for e in case["events"])}
            if answer:
                run = client.get("/api/analytics/runs/" + answer["run_id"]).json()
                case["checks"]["result_status"] = run["status"]
                case["checks"]["inbox_questions"] = len(answer["questions"])
                if name in {"ambiguous_cause", "undefined_outlier"}:
                    case["checks"]["no_unsupported_computation"] = run["status"] == "needs_input" and not run["result"]
                if answer["series"]:
                    series = answer["series"][0]
                    if name == "statistics":
                        stats = series["statistics"]
                        case["checks"]["reference_matches"] = stats["event_count"] == 37251 and stats["missing_count"] == 70 and abs(stats["mean"] - 0.7245121964982135) < 1e-12
                    elif name == "explicit_trend":
                        case["checks"]["reference_matches"] = abs(series["baseline"]["mean"] - 0.7269468483833719) < 1e-12 and abs(series["current"]["mean"] - 0.7250591910828026) < 1e-12 and abs(series["delta"] + 0.0018876573005692787) < 1e-12
                    elif name == "recipe_split":
                        case["checks"]["reference_matches"] = abs(series["group_a"]["mean"] - 0.7259740943299847) < 1e-12 and abs(series["group_b"]["mean"] - 0.7259764473055934) < 1e-12 and abs(series["delta"] - 0.0000023529756086881903) < 1e-12
                conversation = next(e["data"] for e in case["events"] if e["event"] == "conversation")
                restored = client.get("/api/chat/conversations/" + conversation["id"]).json()
                case["checks"]["history_restored"] = restored["messages"][-1].get("analysis", {}).get("run_id") == answer["run_id"]
            report["cases"] = [c for c in report["cases"] if c["name"] != name] + [case]
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"case": name, "seconds": case["seconds"], "checks": case["checks"],
                "terminal": [(e["event"], e["data"].get("message", "")) for e in case["events"] if e["event"] == "error"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
