"""Exercise the existing chat without applying any proposed changes.

Uses the local app's authenticated demo session and its existing personal model
credential. Saves only questions, SSE events and timings, never credentials.
"""
import json
import os
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    ("statistics", "현재 EPM 데이터 전체에서 PARA1의 전체 측정 건수, NULL 건수, 평균 value를 알려줘. 재측정은 모두 포함하고 데이터셋이나 카드는 만들지 마."),
    ("ambiguous_cause", "최근 EPM PARA1이 감소하고 있는데 원인을 찾아줄 수 있어?"),
    ("explicit_trend", "EPM PARA1을 2026-09-01 00:00:00 UTC 이상 2026-09-15 00:00:00 UTC 미만과 2026-09-15 00:00:00 UTC 이상 2026-10-01 00:00:00 UTC 미만으로 비교해줘. 각 기간에서 lot_cd, lot_id, wf_id, die_x, die_y, parameter별 마지막 측정만 사용하고, 같은 시각이면 record_id가 큰 행을 사용해. 마지막 값이 NULL이면 이전 값으로 대체하지 마. 두 기간의 평균, 유효 측정 건수와 평균 차이만 알려줘. 변화 판정이나 원인 추정, 카드 생성은 하지 마."),
    ("recipe_split", "2026-09-01 00:00:00 UTC 이상 2026-10-01 00:00:00 UTC 미만 EPM PARA1에서 oper A의 RCP1과 RCP2를 비교해줘. lot_cd, lot_id, wf_id별로 각 측정 시점 이전의 마지막 oper A 이력의 recipe를 대응해줘. 같은 시각이면 record_id가 큰 이력을 선택해. 해당 공정을 재방문한 wafer는 임의로 처리하지 말고 확인이 필요하다고 알려줘. 측정은 기간 내 lot_cd, lot_id, wf_id, die_x, die_y, parameter별 마지막 건만 사용하고 마지막 NULL도 유지해. 두 그룹 평균, 유효 측정 건수, RCP2-RCP1 차이를 알려주고 원인이나 유의성은 주장하지 마. 데이터셋이나 카드는 만들지 마."),
    ("undefined_outlier", "전체 inline, EPM PARA 중 이상치가 있는 항목을 찾아줘. 아직 이상치 판단 기준이나 규격은 정하지 않았어."),
]


def main():
    report = {"mode": "existing_chat_no_apply", "cases": []}
    destination = ROOT / ".local" / "analysis-question-evaluation.json"
    selected = os.environ.get("ANALYTICS_EVAL_CASE")
    if selected and destination.exists():
        report = json.loads(destination.read_text(encoding="utf-8"))
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=240) as client:
        assert client.get("/api/auth/session").json()["status"] == "authenticated"
        assert client.get("/api/llm/status").json()["configured"]
        for name, question in QUESTIONS:
            if selected and name != selected:
                continue
            started = time.perf_counter()
            case = {"name": name, "question": question, "events": []}
            event = None
            with client.stream("POST", "/api/chat", json={"message": question, "history": []}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        value = json.loads(line[6:])
                        elapsed = round(time.perf_counter() - started, 3)
                        case["events"].append({"event": event, "seconds": elapsed, "data": value})
                        if event in {"proposal", "clarification", "error"}:
                            print(json.dumps({"case": name, "event": event, "seconds": elapsed,
                                              "data": value}, ensure_ascii=False), flush=True)
            case["seconds"] = round(time.perf_counter() - started, 3)
            report["cases"] = [c for c in report["cases"] if c["name"] != name] + [case]
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
