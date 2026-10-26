"""Reproduce extraction using assistant-authored synthetic fixture reports only.

demo/fab/README.md and generator.py document that these are generated fixtures,
not company data. No app DB access; only allowlisted diagnostics are printed.
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.semantic import collector_config as llm
from app.semantic.collector_schemas import ExtractedKnowledge
from app.semantic.evaluation_extraction import EvaluationPassages
from app.semantic.collector_service import extraction_instruction
from app.semantic.schemas import DETAIL_MODELS
from pydantic import ValidationError
import httpx

saved = json.loads((ROOT / ".local/evaluation-example-registration.json").read_text(encoding="utf-8"))
report = saved["reports"][int(sys.argv[1]) if len(sys.argv) > 1 else 0]
assert report["text"].startswith("[합성 DB 예시")
assert "실제 사내 평가 사실이 아니다" in report["text"]
payload = {"question": "합성 평가 사례 정리", "dataset": {"id": "synthetic", "name": "합성 측정", "columns": []},
           "targets": [], "kind": "evaluation",
           "answers": [{"id": "synthetic-answer", "text": report["text"], "targets": []}],
           "allowed_details": list(DETAIL_MODELS["evaluation"].model_fields)}
real_client = httpx.Client
diagnostic = {"input_chars": len(report["text"]), "app_database_access": False, "synthetic_only": True}

class ObservedClient(real_client):
    def send(self, *args, **kwargs):
        response = super().send(*args, **kwargs)
        diagnostic["http_status"] = response.status_code
        original = response.iter_bytes
        def observe(*args, **kwargs):
            chunks = []
            for chunk in original(*args, **kwargs):
                chunks.append(chunk)
                yield chunk
            try:
                value = json.loads(b"".join(chunks))
                choice = value["choices"][0]
                diagnostic["finish_reason"] = choice.get("finish_reason")
                diagnostic["usage"] = {k: v for k, v in value.get("usage", {}).items()
                                       if k in ("prompt_tokens", "completion_tokens", "total_tokens")}
                diagnostic["reasoning_tokens"] = value.get("usage", {}).get("completion_tokens_details", {}).get("reasoning_tokens")
                content = choice["message"]["content"]
                diagnostic["output_chars"] = len(content or "")
                try:
                    EvaluationPassages.model_validate_json(content)
                    diagnostic["schema_valid"] = True
                except ValidationError as exc:
                    diagnostic["validation_errors"] = [{"loc": e["loc"], "type": e["type"]}
                                                       for e in exc.errors(include_input=False, include_url=False)]
            except Exception as exc:
                diagnostic["diagnostic_error_type"] = type(exc).__name__
        response.iter_bytes = observe
        return response

llm.httpx.Client = ObservedClient
started = time.monotonic()
try:
    llm.complete(extraction_instruction("evaluation"), payload, ExtractedKnowledge)
    diagnostic["success"] = True
except Exception as exc:
    diagnostic["success"] = False
    diagnostic["error_type"] = type(exc).__name__
    if isinstance(exc, llm.CollectionError):
        diagnostic["safe_error"] = str(exc)
finally:
    diagnostic["seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(diagnostic, ensure_ascii=False))
    (ROOT / ".local/evaluation-structure-diagnosis.json").write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
