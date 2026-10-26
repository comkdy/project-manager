"""Read-only assertions after the disposable feature-bundle browser walkthrough."""
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def get(port, path):
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
        return json.load(response)


def main():
    ids = json.loads((ROOT / ".local/feature-bundle-qa-ids.json").read_text(encoding="utf-8"))
    runs = get(8001, "/api/analytics/runs")
    original = get(8001, "/api/analytics/runs/" + ids["run_id"])
    candidates = [run for run in runs if run["id"] != ids["run_id"]]
    assert len(candidates) == 1, "Expected one explicitly resumed analysis"
    resumed = get(8001, "/api/analytics/runs/" + candidates[0]["id"])
    before, after = original["request"], resumed["request"]
    stable_fields = [key for key in before if key not in {"min_samples", "absolute_delta", "knowledge_refs"}]
    assert all(before[key] == after[key] for key in stable_fields), "Original conditions changed"
    assert before["min_samples"] is None and before["absolute_delta"] is None
    assert before["knowledge_refs"] == []
    assert after["min_samples"] == 3 and after["absolute_delta"] == 0.5
    assert len(after["knowledge_refs"]) == 1 and after["knowledge_refs"][0]["version"] == 1
    assert original["status"] == resumed["status"] == "completed"
    assert original["result"]["series"][0]["status"] == "needs_criteria"
    assert resumed["result"]["series"][0]["status"] == "stable"
    assert original["result"]["row_count"] == resumed["result"]["row_count"] == 37251
    assert resumed["result"]["knowledge_refs"] == after["knowledge_refs"]
    dataset = get(8001, "/api/datasets/qa-shared/latest")
    assert dataset["version"] == 3, "Restore must create a new version"
    assert [column["name"] for column in dataset["columns"]] == ["lot_cd", "value", "end_dt_tm"]
    health = get(8000, "/api/health")
    assert health == {"status": "ok", "dependencies": {"metadata": "ok", "source": "ok"}}
    result = {
        "status": "passed",
        "original_conditions_preserved": stable_fields,
        "timestamp_precision_preserved": True,
        "original_analysis_unchanged": True,
        "approved_knowledge_references": len(after["knowledge_refs"]),
        "resumed_status": resumed["status"],
        "resumed_verdict": resumed["result"]["series"][0]["status"],
        "row_count": resumed["result"]["row_count"],
        "dataset_restored_as_version": dataset["version"],
        "live_app_health": health,
    }
    (ROOT / "validation/feature-bundle-qa-result-2026-10-23.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
