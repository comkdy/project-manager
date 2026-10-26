"""Synthetic two-turn interpretation through configured AI; no DB writes."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.semantic import collector_config as llm
from app.semantic.collector_schemas import ExtractedKnowledge
from app.semantic.collector_service import extraction_instruction, grounded_proposal
from app.semantic.evaluation_terms import resolve_mentions, term_questions


def main():
    definitions = {}
    for identity, name, aliases, description in [
        ("a-width", "A 공정 선폭", ["alpha", "CD_MAIN"], "A 공정 직후 계측한 선폭이다. 보고서 항목은 CD_MAIN이다."),
        ("b-width", "B 공정 선폭", ["alpha", "CD_B"], "B 공정 직후 계측한 선폭이다. 보고서 항목은 CD_B이다."),
        ("epm-one", "EPM parameter1", ["parameter1"], "Line 밖에서 평가한 EPM 전기적 특성이다."),
        ("operation-a", "operation A", ["A 공정"], "wafer 공정 이력의 A 공정이다."),
    ]:
        definitions[identity] = {"id": identity, "version": 1, "kind": "term", "name": name,
            "aliases": aliases, "description": description, "binding_version": None,
            "binding_fingerprint": "", "binding": None}
    answers = [{"id": "synthetic-original", "text": "operation A에서 recipe1, recipe2, recipe3을 비교했다. Inline CD alpha가 증가했고 EPM parameter1은 감소했다. 장비 영향은 분리하지 못했다.", "targets": []}]
    saved = []
    for index in range(2):
        payload = {"kind": "evaluation", "question": "자연어 평가 사례", "targets": [],
            "dataset": {"context_kind": "evaluation_case", "project_id": None},
            "case_context": {"project_id": None}, "answers": answers,
            "resolve_terms": True, "approved_knowledge": list(definitions.values()), "allowed_details": []}
        extracted = llm.complete(extraction_instruction("evaluation"), payload, ExtractedKnowledge)
        proposal, evidence = grounded_proposal(SimpleNamespace(kind="evaluation"), answers, extracted)
        terms = resolve_mentions(answers, extracted.mentions, definitions)
        assert terms, "No terms extracted"
        assert proposal["content"]["details"].get("inline_observations")
        assert proposal["content"]["details"].get("epm_observations")
        assert all(any(e["quote"] in a["text"] for a in answers) for e in evidence.values())
        alpha = next(t for t in terms if "alpha" in t["term"].lower())
        if index == 0:
            assert alpha["status"] == "ambiguous", alpha
        else:
            assert alpha["status"] == "defined" and alpha["references"][0]["item_id"] == "a-width", alpha
            assert alpha["answer_id"] == "synthetic-clarification", alpha
        saved.append({"turn": index + 1, "extraction": extracted.model_dump(), "terms": terms, "questions": term_questions(terms)})
        answers.append({"id": "synthetic-clarification", "text": "alpha는 A 공정 직후 측정한 선폭이야. 승인 지식의 A 공정 선폭, 보고서 CD_MAIN을 뜻해. B 공정의 CD_B는 아니야. recipe1, recipe2, recipe3은 어떤 실제 데이터 항목인지 지금은 모르겠어. 나중에 확인할게.", "targets": []})
    (ROOT / "validation/natural-evaluation-provider-response.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"synthetic_input_only": True, "database_writes": False, "turns": 2,
              "grounded_observations": True, "ambiguous_alias_question": True, "natural_clarification_resolves_approved_definition": True}
    (ROOT / "validation/natural-evaluation-provider.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
