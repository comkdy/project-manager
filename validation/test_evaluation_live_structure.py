"""Opt-in real-provider verification on synthetic reports and in-memory metadata.

Never patches extraction, creates drafts manually, or approves real app items.
"""
import json
import os
from pathlib import Path
import sys
import time

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/tests"))
from test_semantic_collection import env, BASE, approve
from app.semantic import collector_config as llm
from app.semantic.evaluations import retrieve_evaluation_cases

pytestmark = pytest.mark.skipif(os.environ.get("PM_LIVE_STRUCTURE") != "1", reason="Explicit live synthetic-provider verification only")


@pytest.fixture
def provider(monkeypatch):
    values = dotenv_values(ROOT / ".env")
    config = llm.CollectorConfig()
    assert config.llm_api_key.get_secret_value() and values.get("LLM_BASE_URL") and values.get("LLM_MODEL")
    monkeypatch.setattr(llm, "configuration", lambda: (config, values["LLM_BASE_URL"], values["LLM_MODEL"]))


def check_and_approve(env, receipt, report):
    detail = env.client.get(BASE + f"/questions/{receipt['question_id']}").json()
    assert detail["status"] == "review", [(a["status"], a["error"]) for a in detail["answers"]]
    assert any(a["text"] == report for a in detail["answers"])
    draft = env.client.get(BASE + f"/drafts/{detail['draft']['id']}").json()
    assert not draft["issues"], draft["issues"]
    assert not draft["followups"], draft["followups"]
    sources = {a["id"]: a["text"] for a in detail["answers"]}
    assert all(e["quote"] in sources[e["answer_id"]] and e["value"] in e["quote"] for e in draft["evidence"].values())
    with env.sessions() as db:
        assert not retrieve_evaluation_cases(db, "Inline EPM Recipe", dataset_id="a-data")["cases"]
    env.actor.id, env.actor.role = "reader", "viewer"
    assert approve(env, draft).status_code == 403
    env.actor.id, env.actor.role = "admin", "admin"
    result = approve(env, draft)
    assert result.status_code == 200, result.text
    with env.sessions() as db:
        found = retrieve_evaluation_cases(db, "Inline EPM Recipe", dataset_id="a-data")["cases"]
    assert len(found) == 1 and found[0]["id"] == result.json()["item_id"]
    assert found[0]["details"].get("inline_observations")
    assert found[0]["details"].get("epm_observations")
    assert env.client.get(BASE + f"/questions/{receipt['question_id']}").json()["status"] == "answered"
    return sorted(draft["evidence"])


@pytest.mark.parametrize("index", [0, 1, 2])
def test_original_synthetic_reports_automatically_reach_approval_and_retrieval(env, provider, index):
    saved = json.loads((ROOT / ".local/evaluation-example-registration.json").read_text(encoding="utf-8"))
    report = saved["reports"][index]["text"]
    assert report.startswith("[합성 DB 예시") and "실제 사내 평가 사실이 아니다" in report
    started = time.monotonic()
    response = env.client.post(BASE + "/evaluations", json={"dataset_id": "a-data", "text": report})
    assert response.status_code == 202
    fields = check_and_approve(env, response.json(), report)
    duplicate = env.client.post(BASE + "/evaluations", json={"dataset_id": "a-data", "text": report})
    assert duplicate.json()["status"] == "existing"
    assert duplicate.json()["question_id"] == response.json()["question_id"]
    (ROOT / f"validation/evaluation-live-structure-{index + 1}.json").write_text(json.dumps({
        "synthetic_only": True, "live_metadata_writes": False, "manual_draft_creation": False,
        "provider_extraction": True, "approval_and_retrieval": True, "evidence_fields": fields,
        "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_missing_observation_question_answer_automatically_reaches_review(env, provider):
    report = "검증용 가상 사례: Operation A에서 Recipe1, Recipe2, Recipe3을 비교했다."
    response = env.client.post(BASE + "/evaluations", json={"dataset_id": "a-data", "text": report})
    assert response.status_code == 202
    receipt = response.json()
    detail = env.client.get(BASE + f"/questions/{receipt['question_id']}").json()
    assert detail["draft"] and detail["followups"]
    first = env.client.get(BASE + f"/drafts/{detail['draft']['id']}").json()
    assert approve(env, first).status_code == 422
    observation = "Operation A 평가 관측: Inline CD alpha가 증가했고 EPM parameter1이 감소했다. 장비 영향은 분리하지 못했다."
    answer = env.client.post(BASE + f"/questions/{receipt['question_id']}/answers", json={
        "expected_revision": detail["revision"], "text": observation})
    assert answer.status_code == 202
    check_and_approve(env, receipt, observation)
