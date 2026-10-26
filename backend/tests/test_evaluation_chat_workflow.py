"""Case-guided follow-ups reuse the existing query/dataset/card workflow unchanged."""
from copy import deepcopy
import json

import pytest

from app import evaluation_chat
from test_evaluation_chat import CASE
from test_evaluation_narrative import model_plan
from app.evaluation_narrative import evidence_payload
from test_chat_workflow import api, card_for, counts, dataset, events, mock_llm, query, SQL


@pytest.mark.parametrize("intent,suggestion_index", [("dataset", 0), ("card", 1), ("query", 2)])
def test_case_suggestion_continues_through_existing_skill_and_does_not_apply(api, monkeypatch, intent, suggestion_index):
    ds = dataset(api, "wafer_summary")
    selected = card_for(ds, id="evaluation-selected-card")
    case = deepcopy(CASE)
    case["dataset"] = {"id": ds["id"], "name": ds["name"], "version": ds["version"], "project_id": None}
    monkeypatch.setattr(evaluation_chat, "retrieve", lambda search: {"cases": [case], "warnings": [],
        "context": {"project_id": 1, "dataset_id": ds["id"]}})
    payload=evidence_payload(evaluation_chat.answer_from({'cases':[case],'warnings':[]},{}))
    replies = [{"kind": "evaluation_cases"}, model_plan(payload), {"kind": "route", "intent": intent}]
    if intent == "card":
        replies.append({"kind": "card", "explanation": "선택 데이터셋으로 비교 카드를 제안합니다.",
                        "card": card_for(ds, id="model-id", title="평가 비교 카드")})
    else:
        replies.append(query())
        if intent == "dataset":
            replies.append({"kind": "dataset", "name": "평가 비교 데이터", "explanation": "검토할 비교 데이터를 준비합니다."})
    seen = mock_llm(monkeypatch, replies)
    context = {"project_id": 1, "dataset_id": ds["id"], "card": selected}
    before = counts()
    initial = events(api.post("/api/chat", json={"message": "현재 데이터 관련 평가 사례를 찾아 추가 분석을 추천해줘", **context,
        'history':[{'role':'assistant','content':'withdrawn-old-claim should not reach the explanation model'}]}))
    assert "error" not in initial, initial
    suggestion = initial["evaluation_cases"]["suggestions"][suggestion_index]
    assert counts() == before and len(seen) == 2
    assert initial['evaluation_cases']['narrative']['status']=='ready'
    explanation_context=json.loads(seen[1]['messages'][0]['content'].split('\nRegistered context:\n',1)[1])
    assert set(explanation_context)=={'evidence'}
    assert 'withdrawn-old-claim' not in json.dumps(seen[1]['messages'])
    followup = events(api.post("/api/chat", json={"message": suggestion["question"], **context}))
    assert "error" not in followup, followup
    proposal = followup["proposal"]
    assert proposal["kind"] == intent and counts() == before
    if intent == "card":
        assert proposal["card"]["dataset_id"] == ds["id"]
        assert proposal["card"]["id"] == selected["id"]
    else:
        assert proposal["dataset"]["spec"]["raw_sql"] == SQL
    assert len(seen) == len(replies)
