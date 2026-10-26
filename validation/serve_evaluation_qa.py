"""Disposable evaluation-case QA with fixed model responses and real app paths.

Stop with .local/evaluation-qa.stop. Only this process's random metadata DB is
removed; production metadata and source measurements are never modified.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def install_fixtures():
    from app import assistant
    from app.chat_contracts import OUTPUTS
    from app.semantic import collector_config
    from app.semantic.collector_schemas import ExtractedKnowledge, EvaluationMention
    from serve_feature_bundle_2_qa import install_fixture_adapter

    install_fixture_adapter()
    previous_run = assistant.ModelAdapter.run

    async def run(adapter, skill, extra=None, validate=None):
        if skill == "route":
            message = adapter.request.message
            if "데이터셋" in message and ("만들" in message or "작성" in message):
                return OUTPUTS[skill].validate_python({"kind": "route", "intent": "dataset"}), None
            if "카드" in message and ("만들" in message or "작성" in message):
                return OUTPUTS[skill].validate_python({"kind": "route", "intent": "card"}), None
            if "SQL" in message:
                return OUTPUTS[skill].validate_python({"kind": "route", "intent": "query"}), None
            if "사례" in message or "추천" in message:
                return OUTPUTS[skill].validate_python({"kind": "evaluation_cases"}), None
        return await previous_run(adapter, skill, extra, validate)

    assistant.ModelAdapter.run = run
    collector_config.configured = lambda: True

    def extract(instruction, payload, response_type):
        from app.evaluation_recommendation import Plan, Clarification
        from app.evaluation_datasets import DatasetPlan
        if response_type is DatasetPlan:
            clarification = next((f for f in payload['facts'] if f['kind'] == 'clarification'), None)
            if not clarification:
                return DatasetPlan(summary='분석할 데이터와 표본 대응을 먼저 확인합니다.', questions=[Clarification(
                    question='epm_a와 epm_b는 각각 어떤 항목이며, 같은 wafer와 측정 순서는 어떻게 연결되어 있나요?',
                    reason='사례에 맞는 측정값과 데이터 연결을 확인합니다.')])
            known = next((d for d in payload['datasets'] if d['id'] == 'qa-evaluation-wide'), None)
            if known and '새 데이터셋' not in clarification['text']:
                return DatasetPlan(summary='검증 평가 비교 데이터셋에 필요한 측정값과 표본·시각이 함께 있어 재사용합니다.', dataset_id=known['id'],
                    evidence=[{'reference':'dataset:'+known['id'],'quote':'epm_a'}],alignment_basis={'reference':clarification['reference'],'quote':'같은 wafer'})
            # Test fallback through the real query validator/editor, never create data in this adapter.
            from app import db as database
            from app.models import DatasetVersion
            with database.SessionLocal() as db:
                stored=db.get(DatasetVersion,('qa-evaluation-wide',1))
                spec=stored.spec
            return DatasetPlan(summary='같은 wafer의 두 측정값을 함께 볼 비교 데이터 생성안을 준비했습니다.',
                query={'kind':'query','name':'평가 사례 추천 비교','explanation':'승인된 표본 기준으로 대응합니다.','source_id':spec['source_id'],'sql':spec['raw_sql']},
                evidence=[{'reference':clarification['reference'],'quote':'epm_a'}],alignment_basis={'reference':clarification['reference'],'quote':'같은 wafer'})
        if response_type is Plan:
            clarification = next((f for f in payload['facts'] if f['kind'] == 'clarification'), None)
            if not clarification:
                return Plan(summary='두 측정값과 표본·시간 기준의 연결을 먼저 확인합니다.', questions=[Clarification(
                    question='epm_a와 epm_b는 각각 어떤 항목이며, 같은 표본과 측정 순서는 어떻게 연결되어 있나요?',
                    reason='목표와 선행 측정값, 같은 Lot 및 결과 시각을 구별해야 합니다.', columns=['epm_a','epm_b'])])
            ref = clarification['reference']
            return Plan.model_validate({'summary':'epm_b를 목표로 epm_a의 관계를 확인하고 Lot과 시점을 나누어 ML을 검증합니다.',
                'settings':{'method':'ml_regression','metrics':['epm_b'],'features':['epm_a'],'unit_columns':['lot_id'],'time_column':'measured'},
                'columns':[{'column':c,'role':role,'reason':reason,'evidence':{'reference':ref,'quote':c}} for c,role,reason in [
                    ('epm_b','target','승인한 설명에서 확인할 결과로 지정했습니다.'),('epm_a','feature','결과 전에 확인한 비교 항목입니다.'),
                    ('lot_id','unit','같은 Lot은 학습과 검증에 나누어 넣지 않습니다.'),('measured','time','결과 시각을 기준으로 이후 표본에서 검증합니다.')]],
                'row_basis':{'reference':ref,'quote':'같은 wafer'},'features_precede_target':True,
                'limitations':['합성 데이터의 관측 관계이며 원인 확정이 아닙니다.']})
        answers = payload["answers"]
        fields = {}

        def field(key, fragment):
            for answer in reversed(answers):
                if fragment in answer["text"]:
                    fields[key] = {"value": fragment, "answer_id": answer["id"], "quote": fragment}
                    return True
            return False

        first = answers[0]
        fields["description"] = {"value": first["text"], "answer_id": first["id"], "quote": first["text"]}
        if payload["kind"] != "evaluation":
            return ExtractedKnowledge(name="검증 기준", fields=fields)
        field("details.operation", "operation A")
        field("details.recipe_changes", "recipe1, recipe2, recipe3")
        inline = field("details.inline_observations", "Inline A CD alpha 증가")
        epm = field("details.epm_observations", "EPM parameter1 감소")
        field("details.conditions", "제품 P, 장비 E1, 2026년 9월")
        field("details.evidence", "평가 보고서 EV-001")
        field("details.limitations", "장비 영향은 분리하지 못함")
        missing = [] if inline or epm else ["Inline 또는 EPM에서 관측한 결과를 알려 주세요."]
        mentions = []
        if payload.get("resolve_terms"):
            latest = answers[-1]
            clarified = "alpha는" in latest["text"]
            deferred = any("alpha" in part and "모르" in part for part in latest["text"].split("."))
            mentions = [EvaluationMention(term="alpha" if any("alpha" in a["text"] for a in answers) else "operation A",
                answer_id=latest["id"], quote=latest["text"],
                explanation=latest["text"] if clarified or deferred else "", deferred=deferred)]
        return ExtractedKnowledge(name="공정 A Recipe 평가 사례", fields=fields,
                                  unresolved=missing, followups=missing, mentions=mentions)

    collector_config.complete = extract


def serve():
    import uvicorn
    from serve_feature_bundle_qa import serve as seed_and_serve
    original_run = uvicorn.run

    def launch(*args, **kwargs):
        from app import main as application
        application.DIST = ROOT / "frontend/dist-evaluation"
        install_analysis_fixture()
        install_fixtures()
        original_run(*args, **kwargs)

    uvicorn.run = launch
    seed_and_serve()


def install_analysis_fixture():
    """Two real EPM series, explicitly paired by wafer, in disposable metadata."""
    from sqlalchemy import select, text
    from app import db as database
    from app.models import DatasetVersion, Source
    from app.query import preview_dataset, source_connection
    from app.schemas import DatasetSpec
    with database.SessionLocal() as db:
        source = db.scalar(select(Source).where(Source.table_name == "epm_data",
            Source.connection_key == database.source_database_key()))
    with source_connection() as connection:
        parameters = list(connection.scalars(text("SELECT DISTINCT parameter FROM public.epm_data WHERE parameter IS NOT NULL ORDER BY parameter LIMIT 2")))
        lots = list(connection.scalars(text("SELECT DISTINCT lot_cd FROM public.epm_data WHERE lot_cd IS NOT NULL ORDER BY lot_cd LIMIT 2")))
    assert len(parameters) == 2 and len(lots) == 2
    literal = lambda value: "'" + str(value).replace("'", "''") + "'"
    raw = ("SELECT lot_cd, lot_id, wf_id, "
           f"avg(value) FILTER (WHERE parameter={literal(parameters[0])}) AS epm_a, "
           f"avg(value) FILTER (WHERE parameter={literal(parameters[1])}) AS epm_b, max(end_dt_tm) AS measured "
           f"FROM public.epm_data WHERE lot_cd IN ({','.join(literal(v) for v in lots)}) "
           f"AND parameter IN ({','.join(literal(v) for v in parameters)}) GROUP BY lot_cd, lot_id, wf_id")
    preview = preview_dataset(DatasetSpec(source_id=source.id, raw_sql=raw))
    with database.SessionLocal.begin() as db:
        db.add(DatasetVersion(id="qa-evaluation-wide", version=1, name="검증 평가 비교", project_id=None,
                              spec=preview["spec"], columns=preview["columns"]))
    # An independent SQL reconciliation of the exact source projection.
    with source_connection() as connection:
        expected = [dict(row) for row in connection.execute(text("WITH paired AS (" + raw + ") "
            "SELECT lot_cd, count(*) AS rows, count(epm_a) AS n_a, avg(epm_a) AS mean_a, "
            "count(epm_b) AS n_b, avg(epm_b) AS mean_b FROM paired GROUP BY lot_cd ORDER BY lot_cd")).mappings()]
    (ROOT / ".local/evaluation-qa-wide.json").write_text(json.dumps({
        "dataset_id": "qa-evaluation-wide", "name": "검증 평가 비교", "group_column": "lot_cd",
        "metrics": ["epm_a", "epm_b"], "baseline": lots[0], "comparison": lots[1],
        "source_parameters": parameters, "expected": expected}, default=float, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import URL
    from cryptography.fernet import Fernet

    runtime = json.loads((ROOT / ".local/runtime.json").read_text(encoding="utf-8-sig"))
    assert runtime["marker"] == "wafer-local-demo-v1" and runtime["port"] == 55432
    prefix = "qa_evaluation_"
    name = prefix + uuid.uuid4().hex[:12]
    stop = ROOT / ".local/evaluation-qa.stop"
    stop.unlink(missing_ok=True)

    def url(user, password, database):
        return URL.create("postgresql+psycopg", username=user, password=password,
                          host="127.0.0.1", port=runtime["port"], database=database).render_as_string(hide_password=False)

    env = dict(os.environ)
    env.update(APP_DATABASE_URL=url("wafer_metadata", runtime["app_password"], name),
               SOURCE_DATABASE_URL=url("wafer_source", runtime["source_password"], "wafer_fab_demo"),
               DEMO_MODE="true", AUTH_MODE="local_admin", LLM_BASE_URL="", LLM_MODEL="",
               DB_CREDENTIALS_KEY=Fernet.generate_key().decode(),
               LLM_CREDENTIALS_KEY=Fernet.generate_key().decode(), TABLE_IGNORE="[]",
               ALLOWED_ORIGINS="http://127.0.0.1:8001", PYTHONPATH=str(ROOT / "backend"))
    with psycopg.connect(host="127.0.0.1", port=runtime["port"], dbname="postgres", user="wafer_local_admin",
                         password=runtime["admin_password"], autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER wafer_metadata").format(sql.Identifier(name)))
        child = None
        try:
            with (ROOT / ".local/evaluation-qa.log").open("w", encoding="utf-8") as log:
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve"],
                                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                print("Disposable evaluation QA started; awaiting stop file.", flush=True)
                while child.poll() is None and not stop.exists():
                    time.sleep(0.3)
                if child.poll() not in (None, 0):
                    print("QA startup failed; inspect .local/evaluation-qa.log.", flush=True)
                    return 1
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=30)
            assert name.startswith(prefix) and len(name) == len(prefix) + 12
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            print("Disposable evaluation metadata removed.", flush=True)
    return 0


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        sys.exit(main())
