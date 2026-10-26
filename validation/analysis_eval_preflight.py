"""Local-only inspection of the exact context sent by the evaluation requests."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.assistant import ChatRequest
from app.chat_tools import proposal_catalog, knowledge_context
from app.database_settings import load_active_source
from app.llm_settings import llm_configuration

binding = load_active_source()
catalog, sources = proposal_catalog(ChatRequest(message="분석 검증"), all_datasets=True)
relations = knowledge_context(sources)
endpoint, _ = llm_configuration()
context = {"datasets": catalog, "sources": sources, **relations}
destination = ROOT / ".local" / "analysis-eval-context.json"
destination.write_text(json.dumps(context, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
print(json.dumps({"model_destination_host": urlsplit(endpoint).hostname,
    "source_host": binding.engine.url.host, "source_database": binding.engine.url.database,
    "datasets": [{"id": d["id"], "name": d["name"]} for d in catalog],
    "sources": [{"name": s.get("name"), "table_name": s.get("table_name")} for s in sources],
    "context_keys": list(context), "relation_count": len(relations.get("relations", [])),
    "context_file": str(destination)}, ensure_ascii=False, default=str))
