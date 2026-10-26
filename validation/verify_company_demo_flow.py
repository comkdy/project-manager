"""Real Parquet/PostgreSQL/app flow with renamed columns and synthetic values.

Runs only through company_demo_qa.py flow. Model interpretation is stubbed; SQL,
statistics, ML, questions, approvals, and stored knowledge use production code.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg
from psycopg import sql
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.import_parquet_demo import import_snapshot, register_snapshot, connect, managed_runtime, ImportProblem
from app import db as database

STATE = json.loads((ROOT / ".local/company-demo-qa.json").read_text())
assert make_url(database.settings.app_database_url).database == STATE["flow_metadata"]
FOLDER = ROOT / ".local/imports/company-demo-qa"
REPORT = ROOT / "validation/company-demo-flow.json"


def fixtures():
    frames = {name: [] for name in ("wafer_his", "inline", "epm")}
    for product in ("XXXA", "XXXB"):
        for lot in range(24):
            for wafer in range(5):
                key = {"lot_cd": product, "batch_key": f"{product}-{lot:03}", "wafer_no": f"{wafer + 1:02}"}
                at = datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=lot, hours=wafer)
                recipe = (lot + wafer) % 3
                cd = Decimal("30.0000") + Decimal(recipe) * Decimal("1.2500") + Decimal(wafer) * Decimal("0.1000")
                leakage = 5.0 - float(cd) * .07 + ((lot * 7 + wafer * 3) % 13) * .005
                frames["wafer_his"].append({**key, "process_stage": "ETCH_A", "recipe_name": f"R{recipe+1}",
                    "tool_code": f"TOOL-{lot % 4}", "processed_at": at})
                for operation, value in (("CD_A", cd), ("CD_B", cd + 10)):
                    frames["inline"].append({**key, "step_name": operation, "item_code": "MAIN", "reading": value,
                        "measured_at": at + timedelta(hours=6), "operator_note": None if wafer == 0 else "합성 검증", "valid_flag": True})
                frames["epm"].append({**key, "test_item": "LEAKAGE", "reading": leakage,
                    "tested_at": at + timedelta(hours=12)})
    for name, rows in frames.items():
        directory = FOLDER / name
        directory.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows)
        # Multiple Spark-like part files; leading-zero IDs, decimals, NULLs, UTC timestamps.
        for number, start in enumerate(range(0, len(rows), 73)):
            pq.write_table(table.slice(start, 73), directory / f"part-{number:05}.snappy.parquet", compression="snappy")
    return frames


def main():
    from app.main import app
    from app.semantic import collector_config as llm
    from app.semantic.collector_schemas import ExtractedKnowledge
    from app.semantic.evaluations import retrieve_evaluation_cases
    from app.semantic.service import get_semantic_context
    from app.semantic.schemas import ContextRequest
    from app.assistant import ChatRequest, proposal_context
    from app import chat_data_analysis, chat_history
    from app.chat_data_contracts import DataAnalysisPlan
    from app.analytics.models import AnalysisRun

    original = fixtures()
    try:
        imported = import_snapshot(FOLDER, STATE["imported"])
    except ImportProblem as exc:
        if "이미 있습니다" not in str(exc):
            raise
        with connect(managed_runtime(), STATE["imported"]) as connection:
            imported = connection.execute("SELECT report FROM import_data.snapshot_info").fetchone()[0]
    registered = register_snapshot(STATE["imported"], activate=True)
    assert registered["active"] and registered["table_count"] == 3
    with connect(managed_runtime(), STATE["imported"]) as connection:
        for name, rows in original.items():
            columns = list(rows[0])
            actual = connection.execute(sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier("public", name))).fetchall()
            assert sorted(map(repr, actual)) == sorted(repr(tuple(row[c] for c in columns)) for row in rows), name
    result = {"model_called": False, "synthetic_only": True, "import": imported, "round_trip_values": True,
        "original_column_names": True, "lot_cd_preserved": True, "registration": registered}
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def extract(prompt, payload, schema):
        answer = payload["answers"][-1]
        value = answer["text"]
        return ExtractedKnowledge(name="Inline CD_A MAIN 측정 의미" if "kind=evaluation" not in prompt else "합성 공정 평가",
            fields={"description": {"value": value, "quote": value, "answer_id": answer["id"]}})

    with patch.object(llm, "configured", return_value=True), patch.object(llm, "complete", side_effect=extract), \
            TestClient(app, client=("127.0.0.1", 50100)) as client:
        def api(method, path, body=None, status=200):
            response = client.request(method, "/api" + path, json=body)
            assert response.status_code == status, (path, response.status_code, response.text[:1800])
            return response.json()

        actor = api("GET", "/auth/me")["id"]
        boot = api("GET", "/bootstrap")
        assert {s["table_name"] for s in boot["catalog"]} == {"wafer_his", "inline", "epm"}
        sources = {s["table_name"]: s for s in boot["catalog"]}
        # Saved connection is restored after the in-memory binding is discarded.
        database.active_source = None
        assert api("GET", "/database-status")["profile_id"] == registered["profile_id"]
        assert api("GET", "/database-status")["status"] == "connected"
        projects = api("GET", "/admin/projects")
        if not projects:
            for code in ("XXXA", "XXXB"):
                api("POST", "/admin/projects", {"name": code, "lot_cds": [code]}, status=201)
            projects = api("GET", "/admin/projects")
        project = next(p for p in projects if p["name"] == "XXXA")
        other = next(p for p in projects if p["name"] == "XXXB")
        pid = project["id"]
        one = api("GET", f"/bootstrap?project_id={pid}")
        assert len(one["datasets"]) == 3
        for ds in one["datasets"]:
            page = api("POST", "/query", {"dataset_id": ds["id"], "dataset_version": ds["version"], "context": {"project_id": pid, "limit": 50}})
            assert page["rows"] and all(r["lot_cd"] == "XXXA" for r in page["rows"])
        joined_sql = """SELECT h.lot_cd,h.batch_key,h.wafer_no,h.recipe_name,h.tool_code,
            i.reading AS cd_value,e.reading AS leakage_value,e.tested_at
            FROM public.wafer_his h JOIN public.inline i
            ON h.batch_key=i.batch_key AND h.wafer_no=i.wafer_no
            JOIN public.epm e ON h.batch_key=e.batch_key AND h.wafer_no=e.wafer_no
            WHERE h.process_stage='ETCH_A' AND i.step_name='CD_A' AND i.item_code='MAIN'
            AND e.test_item='LEAKAGE' AND h.processed_at<i.measured_at AND i.measured_at<e.tested_at"""
        ds = api("POST", "/datasets", {"name": "합성 시연 공정-CD_A MAIN-LEAKAGE", "project_id": pid,
            "spec": {"source_id": sources["wafer_his"]["id"], "raw_sql": joined_sql}}, status=201)
        page = api("POST", "/query", {"dataset_id": ds["id"], "dataset_version": ds["version"], "context": {"project_id": pid, "limit": 300}})
        assert len(page["rows"]) == 120 and all(r["lot_cd"] == "XXXA" for r in page["rows"])
        assert ds["id"] not in {d["id"] for d in api("GET", f"/bootstrap?project_id={other['id']}")["datasets"]}
        card = {"id": "company-qa-scatter", "title": "CD_A MAIN과 LEAKAGE", "dataset_id": ds["id"],
            "dataset_version": ds["version"], "kind": "scatter", "x": "cd_value", "y": "leakage_value", "color": "recipe_name"}
        chart = api("POST", "/cards/query", {"card": card, "context": {"project_id": pid}})
        assert chart["rows"]
        board = api("GET", f"/dashboards?project_id={pid}")
        saved = api("PUT", "/dashboards/" + board["id"], {"version": board["version"], "cards": [card],
            "layout": [{"i": card["id"], "x": 0, "y": 0, "w": 6, "h": 5}]})
        assert saved["cards"][0]["title"] == card["title"]

        base = "/semantic/collection"
        question = api("POST", base + "/questions", {"dataset_id": ds["id"], "title": "CD_A MAIN은 어떤 컬럼과 공정인가요?",
            "reason": "실제 컬럼의 의미 확인", "kind": "term", "topic": "column_meaning", "subject": "CD_A MAIN"}, status=201)
        answer_text = "CD_A MAIN은 inline 테이블의 step_name이 CD_A이고 item_code가 MAIN인 reading입니다. operation과 parameter의 실제 컬럼은 step_name과 item_code이며 두 값을 함께 구분합니다. lot_cd는 프로젝트, batch_key와 wafer_no는 Wafer 연결 키입니다."
        api("POST", base + f"/questions/{question['id']}/answers", {"expected_revision": question["revision"], "text": answer_text}, status=202)
        q = api("GET", base + f"/questions/{question['id']}")
        assert q["draft"] and q["draft"]["status"] == "pending", q
        draft = api("GET", base + "/drafts/" + q["draft"]["id"])
        knowledge = api("POST", base + f"/drafts/{draft['id']}/review", {"expected_revision": draft["revision"], "decision": "approve", "note": "합성 데이터 컬럼 의미 확인"})
        assert knowledge["status"] == "approved"
        request = ChatRequest(message="CD_A MAIN과 LEAKAGE의 현재 관계를 확인해줘", project_id=pid, dataset_id=ds["id"])
        context = proposal_context(request, actor)
        assert knowledge["item_id"] in {k["id"] for k in context["semantic_context"]["items"]}

        runs = []
        for method, settings in (
            ("correlation", {"method": "correlation", "metrics": ["cd_value", "leakage_value"]}),
            ("group_compare", {"method": "group_compare", "metrics": ["cd_value", "leakage_value"], "group_column": "recipe_name"}),
            ("ml_regression", {"method": "ml_regression", "metrics": ["leakage_value"], "features": ["cd_value", "recipe_name", "tool_code"],
                "unit_columns": ["batch_key"], "time_column": "tested_at"}),
        ):
            message = f"[합성 검증·모델 미호출] {method}. 한 행은 batch_key와 wafer_no로 구분한 Wafer입니다. cd_value는 CD_A MAIN입니다. recipe_name과 tool_code, cd_value는 leakage_value보다 먼저 측정되었습니다. batch_key로 분리하고 tested_at 시간 순서로 검증합니다. 추가 필터는 없습니다."
            request = ChatRequest(message=message, project_id=pid, dataset_id=ds["id"], save_history=True)
            evidence = {"reference": "user", "quote": message}
            roles = [(c, "target") for c in settings["metrics"]] + [(c, "feature") for c in settings.get("features", [])] + [(c, "unit") for c in settings.get("unit_columns", [])]
            if settings.get("group_column"):
                roles.append((settings["group_column"], "group"))
            if settings.get("time_column"):
                roles.append((settings["time_column"], "time"))

            class Adapter:
                actor_id = actor
                async def run(self, stage, extra=None, validate=None):
                    assert stage == "data_analysis_plan"
                    current = next(d for d in extra["data_analysis_context"]["datasets"] if d["id"] == ds["id"])
                    value = DataAnalysisPlan(kind="data_analysis_plan", dataset_id=ds["id"], dataset_version=current["version"], plan={
                        "summary": "합성 데이터의 공정 조건과 CD_A MAIN, LEAKAGE 관계 검증", "settings": settings,
                        "row_basis": evidence, "features_precede_target": method == "ml_regression",
                        "columns": [{"column": c, "role": role, "reason": "명시된 검증 조건", "evidence": evidence} for c, role in roles],
                        "limitations": ["관측 연관성이며 인과효과로 단정하지 않습니다."]})
                    return value, validate(value)

            async def collect():
                return {k: v async for k, v in chat_data_analysis.run_workflow(request, Adapter())}

            conversation, _ = chat_history.begin_turn(request, actor)
            events = asyncio.run(collect())
            assert "data_analysis" in events, events
            answer = events["data_analysis"]
            chat_history.finish_turn(conversation["id"], conversation["revision"], "data_analysis", answer)
            for _ in range(90):
                answer = api("GET", f"/chat/data-analysis/{answer['run_id']}?project_id={pid}")
                if answer["status"] not in {"queued", "running", "cancelling"}:
                    break
                time.sleep(.2)
            assert answer["status"] == "completed", answer
            assert answer["result"]["row_count"] == 120, answer
            restored = api("GET", "/chat/conversations/" + conversation["id"])["messages"][-1]["data_analysis"]
            assert restored["status"] == "completed" and restored["result"] == answer["result"]
            runs.append({"method": method, "run_id": answer["run_id"], "status": answer["status"], "rows": answer["result"]["row_count"]})
            if method == "correlation":
                with connect(managed_runtime(), STATE["imported"]) as c:
                    expected = c.execute("SELECT corr(cd_value,leakage_value) FROM (" + joined_sql + ") t WHERE lot_cd='XXXA'").fetchone()[0]
                assert abs(expected - answer["result"]["correlation"]["pearson"]) < 1e-12
            if method == "ml_regression":
                assert answer["result"]["ml"]["status"] in {"validated", "not_better_than_baseline"}
                pending = api("POST", f"/evaluation-analysis/{answer['run_id']}/submit-review", {"expected_revision": answer["revision"],
                    "workspace_token": answer["workspace_token"], "note": "합성 검증 결과를 확인 요청에서 검토"}, status=201)
                assert pending["status"] == "pending"
                draft = api("GET", base + "/drafts/" + pending["draft_id"])
                approved = api("POST", base + f"/drafts/{draft['id']}/review", {"expected_revision": draft["revision"], "decision": "approve", "note": "현재 합성 데이터의 실제 통계·ML 결과 확인"})
                assert approved["status"] == "approved"
                with database.SessionLocal() as session:
                    found = retrieve_evaluation_cases(session, "CD_A MAIN leakage_value recipe_name", dataset_id=ds["id"], project_id=pid)
                assert approved["item_id"] in {c["id"] for c in found["cases"]}, found
                result["approved_result"] = approved
        result.update(project_boundaries=True, dataset_join=True, card_edit=True, natural_answer_approval=True,
            approved_knowledge_in_chat_context=True, runs=runs, chat_history=True, metadata_database=STATE["flow_metadata"])
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"round_trip": True, "runs": runs, "approval": result["approved_result"], "model_called": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
