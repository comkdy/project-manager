"""One synthetic extraction through the configured provider; no app DB writes."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.semantic import collector_config as llm
from app.semantic.collector_schemas import ExtractedKnowledge
from app.semantic.collector_service import extraction_instruction, grounded_proposal
from app.semantic.schemas import DETAIL_MODELS
from app.semantic.service import definition_issues

assert llm.configured(), "Existing shared provider configuration is required"
text = ("operation A에서 recipe1, recipe2, recipe3으로 변경해 평가했다. "
        "inline operation A CD의 alpha para가 증가했고 EPM의 parameter1이 점점 감소했다. "
        "장비 영향은 분리하지 못했다.")
answers = [{"id": "synthetic-evaluation-answer", "text": text, "targets": []}]
response_path = ROOT / "validation/evaluation-provider-response.json"
replay = "--replay" in sys.argv
if replay:
    extracted = ExtractedKnowledge.model_validate_json(response_path.read_text(encoding="utf-8"))
else:
    extracted = llm.complete(extraction_instruction("evaluation"), {
        "question": "공정 평가 사례를 원문 근거와 함께 정리해 주세요.",
        "dataset": {"id": "synthetic-data", "name": "검증용 측정", "columns": []},
        "targets": [], "kind": "evaluation", "answers": answers,
        "allowed_details": list(DETAIL_MODELS["evaluation"].model_fields),
    }, ExtractedKnowledge)
    response_path.write_text(json.dumps(extracted.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
proposal, evidence = grounded_proposal(SimpleNamespace(kind="evaluation"), answers, extracted)
details = proposal["content"]["details"]
issues = definition_issues(None, SimpleNamespace(id="synthetic-item", kind="evaluation"),
                          SimpleNamespace(content=proposal["content"]), {"sources": {}})
assert not issues, "Provider extraction still needs review: " + "; ".join(issues)
assert "alpha" in details["inline_observations"]
assert "parameter1" in details["epm_observations"]
assert not details.get("conditions") and not details.get("evidence"), "Unprovided evaluation conditions must remain empty"
assert all(value["quote"] in text and value["value"] in value["quote"] for value in evidence.values())
report = {"synthetic_input_only": True, "app_database_writes": False,
          "configured_provider_response_verified": True, "replayed_saved_response": replay,
          "grounded_extraction": True,
          "operation_and_both_observations": True, "unknown_conditions_uninvented": True,
          "extracted_fields": sorted(evidence), "approval_issues": issues}
(ROOT / "validation/evaluation-provider.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
