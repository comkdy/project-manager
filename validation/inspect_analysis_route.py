"""Inspect only final JSON and provider response metadata, never credentials/reasoning."""
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx
from evaluate_analysis_questions import QUESTIONS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.assistant import ChatRequest
from app.chat_workflow import ModelAdapter
from app.llm_settings import llm_configuration, load_key, validate_llm_url


async def main():
    async with httpx.AsyncClient(trust_env=False) as local:
        session = (await local.get("http://127.0.0.1:8000/api/auth/session")).json()
    user_id = session["user"]["id"]
    key, _ = load_key(user_id)
    base, model = llm_configuration()
    observations = []
    async def inspect(response):
        await response.aread()
        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        observations.append({"finish_reason": choice.get("finish_reason"), "content_type": type(message.get("content")).__name__,
            "error": str(data.get("error", {}).get("message", "")).replace(key, "[redacted]")[:1000],
            "usage": data.get("usage"), "tool_call_count": len(message.get("tool_calls") or []), "content": message.get("content")})
    async with httpx.AsyncClient(trust_env=False, timeout=90, event_hooks={"response": [inspect]}) as client:
        for name, question in QUESTIONS:
            if name not in sys.argv[1:]:
                continue
            start = time.perf_counter()
            adapter = ModelAdapter(client, validate_llm_url(base), model, {"Authorization": f"Bearer {key}"}, ChatRequest(message=question), {})
            try:
                value, _ = await adapter.run("route")
                result = value.model_dump(mode="json")
            except ValueError as error:
                result = {"error": str(error)}
            print(json.dumps({"case": name, "seconds": time.perf_counter()-start, "result": result, "response_metadata": observations}, ensure_ascii=True), flush=True)
            observations.clear()


if __name__ == "__main__":
    asyncio.run(main())
