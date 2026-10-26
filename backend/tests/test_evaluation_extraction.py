"""Grounding is deterministic even when a model selects disjoint source text."""
import json

import pytest

from app.semantic import collector_config as llm
from app.semantic.collector_schemas import ExtractedKnowledge
from app.semantic.evaluation_extraction import EvaluationPassages, prepare, restore
from test_collection_transport import reply, transport


def test_passages_preserve_decimals_newlines_and_answer_attribution():
    answers = [{"id": "first", "text": "공정 A 비교.\nInline 30.459220 증가. EPM 0.724474 유지."},
               {"id": "second", "text": "한계: 장비 효과는 분리하지 못했다."}]
    request, passages = prepare({"answers": answers})
    assert len(request["source_passages"]) == 4
    assert request["source_passages"][1]["text"] == "Inline 30.459220 증가."
    selected = EvaluationPassages(name="평가", fields={"details.inline_observations": "a1p2", "details.limitations": "a2p4"})
    value = restore(selected, passages)
    assert value.fields["details.inline_observations"].value == "Inline 30.459220 증가."
    assert value.fields["details.limitations"].answer_id == "second"
    by_id = {a["id"]: a["text"] for a in answers}
    assert all(e.value == e.quote and e.quote in by_id[e.answer_id] for e in value.fields.values())


def test_long_source_passages_stay_within_field_limits_without_rewriting():
    text = "공정A" * 2000
    _, passages = prepare({"answers": [{"id": "answer", "text": text}]})
    assert len(passages) == 6
    assert "".join(e["quote"] for e in passages.values()) == text
    assert all(len(e["value"]) <= 1000 for e in passages.values())


def test_unknown_reference_or_computed_result_cannot_be_invented():
    _, passages = prepare({"answers": [{"id": "answer", "text": "Inline 증가."}]})
    with pytest.raises(ValueError):
        restore(EvaluationPassages(name="평가", fields={"description": "invented"}), passages)
    with pytest.raises(ValueError):
        EvaluationPassages(name="평가", fields={"details.analysis_result": "a1p1"})


def test_actual_transport_uses_compact_references_and_restores_existing_contract(monkeypatch):
    response = {"name": "평가", "fields": {"description": "a1p2", "details.operation": "a1p1",
               "details.inline_observations": "a1p2"}}
    requests = transport(monkeypatch, lambda *_: reply(json.dumps(response)))
    value = llm.complete("legacy instruction", {"kind": "evaluation", "dataset": {"private_metadata": "do-not-send"},
        "answers": [{"id": "answer", "text": "공정 A. Inline 30.1 증가."}]}, ExtractedKnowledge)
    assert isinstance(value, ExtractedKnowledge)
    assert value.fields["description"].quote == value.fields["description"].value == "Inline 30.1 증가."
    sent = requests[0]["messages"][1]["content"]
    assert "source_passages" in sent and "do-not-send" not in sent


def test_other_knowledge_types_keep_their_existing_extraction_contract(monkeypatch):
    response = {"name": "설명", "fields": {"description": {"value": "설명", "quote": "설명", "answer_id": "answer"}}}
    requests = transport(monkeypatch, lambda *_: reply(json.dumps(response)))
    value = llm.complete("legacy instruction", {"kind": "term", "answers": [{"id": "answer", "text": "설명"}]}, ExtractedKnowledge)
    assert value.fields["description"].value == "설명"
    assert "source_passages" not in requests[0]["messages"][1]["content"]


def test_bad_reference_is_safe_failure_instead_of_an_ungrounded_draft(monkeypatch):
    transport(monkeypatch, lambda *_: reply('{"name":"평가","fields":{"description":"missing"}}'))
    with pytest.raises(llm.CollectionError, match="형식"):
        llm.complete("instruction", {"kind": "evaluation", "answers": [{"id": "answer", "text": "원문"}]}, ExtractedKnowledge)


def test_missing_fields_can_be_null_without_creating_facts():
    _, passages = prepare({'answers': [{'id': 'answer', 'text': 'A가 B에 영향을 준다.'}]})
    value = restore(EvaluationPassages(name='관계', fields={'description': 'a1p1', 'details.conditions': None}), passages)
    assert set(value.fields) == {'description'}


def test_invented_term_is_automatically_repaired_once_from_original_passages(monkeypatch):
    bad = {'name': '관계', 'fields': {'description': 'a1p1'}, 'mentions': [{'term': '원문에 없는 항목', 'passage_id': 'a1p1'}]}
    good = {'name': '관계', 'fields': {'description': 'a1p1'}, 'mentions': [{'term': 'A', 'passage_id': 'a1p1'}]}
    requests = transport(monkeypatch, lambda n, _: reply(json.dumps(bad if n == 1 else good)))
    value = llm.complete('', {'kind': 'evaluation', 'answers': [{'id': 'answer', 'text': 'A가 B에 영향을 준다.'}]}, ExtractedKnowledge)
    assert value.mentions[0].term == 'A' and len(requests) == 2
    assert '원문에 없는 항목' not in str(requests[1])


def test_repeated_ungrounded_response_is_bounded_and_never_saved(monkeypatch):
    requests = transport(monkeypatch, lambda *_: reply('{"name":"관계","fields":{"description":"missing"}}'))
    with pytest.raises(llm.CollectionError, match='형식'):
        llm.complete('', {'kind': 'evaluation', 'answers': [{'id': 'answer', 'text': 'A가 B에 영향을 준다.'}]}, ExtractedKnowledge)
    assert len(requests) == 2
