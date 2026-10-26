"""Ground example reports in the active app's read-only source queries."""
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000/api"
client = httpx.Client(timeout=90, trust_env=False)


def get(path):
    response = client.get(API + path)
    response.raise_for_status()
    return response.json()


def preview(source_id, sql):
    response = client.post(API + "/datasets/preview", json={"spec": {"source_id": source_id, "raw_sql": sql}, "context": {"limit": 100}})
    if response.status_code != 200:
        raise RuntimeError(f"Source query failed ({response.status_code}): {response.text[:500]}")
    return response.json()["rows"]


def paired_sql(process, inline_operation, epm_parameter):
    # Parameters are fixed identifiers selected from the observed demo catalog.
    assert (process, inline_operation, epm_parameter) in {
        ("oper A", "oper A CD", "PARA1"), ("oper B", "oper B CD", "PARA3")}
    return f"""WITH i AS (
 SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y) lot_cd,lot_id,wf_id,die_x,die_y,value,end_dt_tm
 FROM public.inline_data WHERE operation='{inline_operation}' AND parameter='MAIN'
 ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,end_dt_tm DESC,record_id DESC
), e AS (
 SELECT DISTINCT ON (lot_cd,lot_id,wf_id,die_x,die_y) lot_cd,lot_id,wf_id,die_x,die_y,value,end_dt_tm
 FROM public.epm_data WHERE parameter='{epm_parameter}'
 ORDER BY lot_cd,lot_id,wf_id,die_x,die_y,end_dt_tm DESC,record_id DESC
), w AS (
 SELECT i.lot_cd,i.lot_id,i.wf_id,count(*) AS paired_sites,avg(i.value) AS inline_mean,avg(e.value) AS epm_mean,
 min(i.end_dt_tm) AS first_inline,max(e.end_dt_tm) AS last_epm
 FROM i JOIN e USING(lot_cd,lot_id,wf_id,die_x,die_y)
 WHERE i.value IS NOT NULL AND e.value IS NOT NULL AND e.end_dt_tm>=i.end_dt_tm
 GROUP BY i.lot_cd,i.lot_id,i.wf_id
), matched AS (
 SELECT DISTINCT ON(w.lot_cd,w.lot_id,w.wf_id) w.*,h.recipe
 FROM w JOIN public.wafer_his h ON h.lot_cd=w.lot_cd AND h.lot_id=w.lot_id AND h.wf_id=w.wf_id
 AND h.operation='{process}' AND h.end_dt_tm<=w.first_inline
 WHERE NOT EXISTS (SELECT 1 FROM public.wafer_his h2 WHERE h2.lot_cd=w.lot_cd AND h2.lot_id=w.lot_id
 AND h2.wf_id=w.wf_id AND h2.operation='{process}' AND h2.end_dt_tm>w.first_inline AND h2.end_dt_tm<=w.last_epm)
 ORDER BY w.lot_cd,w.lot_id,w.wf_id,h.end_dt_tm DESC,h.record_id DESC
) """


def reports(observations, sources):
    common = ("이 사례는 현재 연결된 합성 테스트 DB를 직접 조회해 작성한 사용 예시이며 실제 사내 평가 사실이 아니다. "
        "lot_cd, lot_id, wf_id, die_x, die_y가 같은 위치를 대응시켰다. 각 위치의 마지막 측정은 end_dt_tm 내림차순, "
        "동일 시각에는 record_id 내림차순으로 선택했고 마지막 NULL을 과거 값으로 대체하지 않았다. "
        "두 측정이 모두 유효하고 EPM 시각이 Inline 이후인 위치만 사용했다. 위치 평균을 먼저 wafer별로 계산한 후 wafer를 같은 가중치로 평균했다. "
        "Recipe는 첫 Inline 측정 이전 해당 공정의 마지막 이력으로 대응했으며 Inline~EPM 사이에 해당 공정을 재방문한 wafer는 제외했다.")
    limitation = ("관측 비교이며 인과효과나 통계적 유의성을 판정하지 않았다. wafer는 동일 lot 안에서 독립적이지 않을 수 있다. "
        "제품·기간·다른 공정과의 교란을 조정하지 않았고, 장비 컬럼이 없어 장비 영향을 평가할 수 없다. "
        "Recipe 이름의 숫자를 공정 강도나 연속적인 설정값으로 해석하지 않는다. 실제 업무 적용 전에 담당자 검토가 필요하다.")
    rows_a, rows_b, lot_rows = (observations[key] for key in ("recipe_a", "recipe_b", "same_recipe_lot"))
    def recipe_summary(rows, operation, parameter):
        return "\n".join(f"{r['recipe']}: wafer {r['wafers']}장, 유효 대응 위치 {int(r['paired_sites'])}개, "
            f"Inline {operation} MAIN의 wafer 평균 {r['inline_mean']:.6f}, EPM {parameter}의 wafer 평균 {r['epm_mean']:.6f}." for r in rows)
    def period(rows):
        return f"조회된 첫 Inline {min(r['first_inline'] for r in rows)}부터 마지막 EPM {max(r['last_epm'] for r in rows)}까지(UTC)."
    evidence = "근거: 2026-10-24 앱의 읽기 전용 조회로 public.wafer_his, public.inline_data, public.epm_data를 집계했다."
    a1, a3 = rows_a[0], rows_a[2]
    text_a = ("[합성 DB 예시 01] oper A Recipe별 Inline 차이와 EPM PARA1 비교\n"
        "평가 공정은 oper A이며, RCP1/RCP2/RCP3을 사용한 wafer 집단을 비교했다. 한 wafer의 Recipe를 순차 변경한 실험은 아니다.\n"
        + recipe_summary(rows_a, "oper A CD", "PARA1") + "\n"
        f"Inline oper A CD MAIN에서 RCP3 평균은 RCP1보다 {a3['inline_mean']-a1['inline_mean']:.6f} 높았다. "
        f"EPM PARA1의 RCP3-RCP1 평균 차이는 {a3['epm_mean']-a1['epm_mean']:.6f}였다. "
        "Inline의 Recipe별 평균 차이가 EPM PARA1에서 같은 방향의 뚜렷한 평균 차이로 이어졌다고 단정할 수 없다.\n"
        "조건: lot_cd XXXA/XXXB/XXXC 전체, 관측 단위는 wafer. " + period(rows_a) + "\n" + common + "\n"
        "다음 확인 제안: lot_cd·측정 기간을 맞춘 뒤 Recipe별 차이가 유지되는지 비교한다.\n" + evidence + "\n한계: " + limitation)
    text_b = ("[합성 DB 예시 02] oper B CD MAIN과 EPM PARA3의 동행 관계\n"
        "평가 공정은 oper B이다. RCP1/RCP2/RCP3별로 Inline oper B CD MAIN과 EPM PARA3의 관계를 비교했다.\n"
        + recipe_summary(rows_b, "oper B CD", "PARA3") + "\n"
        + " ".join(f"{r['recipe']} 내 wafer 평균의 Inline MAIN–EPM PARA3 Pearson 상관계수는 {r['pearson']:.4f}였다." for r in rows_b) + "\n"
        "각 Recipe 집단 안에서 Inline MAIN이 높은 wafer는 EPM PARA3도 높은 경향이 관측됐다. Inline 변화가 EPM의 원인임을 입증한 것은 아니다.\n"
        "조건: lot_cd XXXA/XXXB/XXXC 전체, wafer 평균끼리 대응. " + period(rows_b) + "\n" + common + "\n"
        "다음 확인 제안: 동일 lot·기간 안에서도 이 관계가 유지되는지, 특정 lot를 제외하면 관계가 달라지는지 확인한다.\n" + evidence + "\n한계: " + limitation)
    lines = []
    for recipe in ("RCP1", "RCP2", "RCP3"):
        target = next(r for r in lot_rows if r["recipe"] == recipe and r["cohort"] == "XXXB7")
        control = next(r for r in lot_rows if r["recipe"] == recipe and r["cohort"] == "other_XXXB")
        lines.append(f"{recipe}: XXXB7 {target['wafers']}장 / 같은 lot_cd XXXB의 다른 lot {control['wafers']}장. "
            f"Inline oper A CD MAIN 평균은 {target['inline_mean']:.6f} / {control['inline_mean']:.6f}, "
            f"EPM PARA1 평균은 {target['epm_mean']:.6f} / {control['epm_mean']:.6f}였다.")
    text_lot = ("[합성 DB 예시 03] 같은 oper A Recipe에서도 XXXB7의 Inline·EPM이 함께 높은 사례\n"
        "평가 공정은 oper A이며 Recipe를 변경한 실험이 아니다. 각 RCP1/RCP2/RCP3 안에서 XXXB7과 같은 lot_cd XXXB의 다른 lot를 비교했다.\n"
        + "\n".join(lines) + "\n"
        "세 Recipe 집단 모두 XXXB7의 Inline oper A CD MAIN 평균과 EPM PARA1 평균이 비교군보다 높았다. "
        "이 관측은 Recipe 이름만으로 wafer의 특성 변동을 충분히 설명할 수 없다는 추가 검토의 출발점이다. 원인은 미확인이다.\n"
        "조건: lot_cd XXXB, XXXB7 12장과 같은 Recipe의 다른 완료 wafer를 비교. " + period(lot_rows) + "\n" + common + "\n"
        "다음 확인 제안: XXXB7과 가까운 공정·측정 기간의 lot만 남겨 재비교하고, 공정 이력과 다른 Inline 항목을 함께 확인한다. "
        "XXXB7 집단은 Recipe당 4장으로 작으며 비교군의 기간을 일치시키지 않았고 합성 데이터에 의도한 특성 이동이 포함될 수 있다.\n"
        + evidence + "\n한계: " + limitation)
    return [{"dataset_id": sources["wafer_his"], "text": text_a},
            {"dataset_id": sources["inline_data"], "text": text_b},
            {"dataset_id": sources["epm_data"], "text": text_lot}]


def finish_grounded_drafts():
    """Fallback only for this task's failed contributions; never approves them."""
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy import select
    from types import SimpleNamespace
    from app import db as database
    from app.database_settings import load_active_source
    from app.models import User
    from app.semantic.collector_models import CollectedDraft, KnowledgeAnswer
    from app.semantic.collector_schemas import ExtractedKnowledge
    from app.semantic.collector_service import audit, draft_issues, future, get_question, grounded_proposal

    saved = json.loads((ROOT / ".local/evaluation-example-registration.json").read_text(encoding="utf-8"))
    actor = get("/auth/session")["user"]["id"]
    binding = load_active_source()
    completed = []
    with database.use_source(binding):
        with database.SessionLocal.begin() as db:
            user = db.get(User, actor)
            assert user and user.active and user.role == "admin"
            for index, (entry, receipt) in enumerate(zip(saved["reports"], saved["receipts"])):
                question = get_question(db, receipt["question_id"], lock=True)
                answer = db.scalar(select(KnowledgeAnswer).where(KnowledgeAnswer.id == receipt["answer_id"]).with_for_update())
                assert answer.question_id == question.id and answer.text == entry["text"] and answer.user_id == actor
                existing = db.scalar(select(CollectedDraft).where(CollectedDraft.answer_id == answer.id))
                if existing:
                    completed.append({"question_id": question.id, "draft_id": existing.id, "status": existing.status, "existing": True})
                    continue
                assert question.status == "open" and answer.status == "error" and not future(answer.lease_until), "Provider still owns this answer; retry later"
                text = answer.text
                lines = text.splitlines()
                numeric = "\n".join(line for line in lines if line.startswith(("RCP1:", "RCP2:", "RCP3:")))
                assert numeric in text
                condition_index = next(i for i, line in enumerate(lines) if line.startswith("조건:"))
                if index == 0:
                    observation = next(line for line in lines if line.startswith("Inline oper A CD MAIN에서"))
                elif index == 1:
                    observation = next(line for line in lines if line.startswith("RCP1 내 wafer"))
                else:
                    observation = next(line for line in lines if line.startswith("세 Recipe 집단"))
                fields = {
                    "description": lines[0] + "\n" + lines[1],
                    "details.operation": "oper B" if index == 1 else "oper A",
                    "details.recipe_changes": lines[1],
                    "details.inline_observations": numeric,
                    "details.epm_observations": observation,
                    "details.conditions": lines[condition_index] + "\n" + lines[condition_index + 1],
                    "details.evidence": next(line for line in lines if line.startswith("근거:")),
                    "details.limitations": text.split("한계: ", 1)[1],
                }
                assert all(value and value in text for value in fields.values())
                extracted = ExtractedKnowledge(name=lines[0], fields={key: {
                    "value": value, "quote": value, "answer_id": answer.id} for key, value in fields.items()})
                proposal, evidence = grounded_proposal(SimpleNamespace(kind="evaluation"), [{"id": answer.id, "text": text}], extracted)
                draft = CollectedDraft(question_id=question.id, answer_id=answer.id, proposal=proposal,
                    original_proposal=proposal, evidence=evidence, followups=[])
                db.add(draft)
                db.flush()
                issues = draft_issues(db, draft)
                assert not issues, issues
                answer.status, answer.error, answer.lease_token, answer.lease_until = "ready", "", None, None
                question.status, question.revision = "review", question.revision + 1
                question.reason += " 자동 정리 오류 후 등록 원문의 정확한 인용으로 직접 정리한 예시 초안입니다."
                audit(db, actor, "structure", answer.id, question_id=question.id,
                    method="assistant_grounded_example", provider_completed=False, approved=False)
                completed.append({"question_id": question.id, "draft_id": draft.id, "status": "review", "issues": issues})
    print(json.dumps(completed, ensure_ascii=False))


def main():
    if "--finish-drafts" in sys.argv:
        finish_grounded_drafts()
        return
    bootstrap = get("/bootstrap")
    sources = {row["table_name"]: row["id"] for row in bootstrap["catalog"] if row["schema_name"] == "public"}
    assert all(name in sources for name in ("inline_data", "epm_data", "wafer_his"))
    if "--register" in sys.argv:
        observations = json.loads((ROOT / ".local/evaluation-example-observations.json").read_text(encoding="utf-8"))
        entries = reports(observations, sources)
        receipt_path = ROOT / ".local/evaluation-example-registration.json"
        receipts = []
        for entry in entries:
            assert len(entry["text"]) <= 6000
            response = client.post(API + "/semantic/collection/evaluations", json=entry)
            response.raise_for_status()
            receipt = {"title": entry["text"].splitlines()[0], **response.json()}
            receipts.append(receipt)
            receipt_path.write_text(json.dumps({"reports": entries, "receipts": receipts}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(receipt, ensure_ascii=False), flush=True)
        return
    if "--status" in sys.argv:
        receipts = json.loads((ROOT / ".local/evaluation-example-registration.json").read_text(encoding="utf-8"))["receipts"]
        values = []
        for receipt in receipts:
            q = get("/semantic/collection/questions/" + receipt["question_id"])
            value = {"title": receipt["title"], "question_id": q["id"], "status": q["status"],
                     "answers": [{k: a[k] for k in ("status", "error")} for a in q["answers"]]}
            if q.get("draft"):
                d = get("/semantic/collection/drafts/" + q["draft"]["id"])
                value.update(draft_id=d["id"], name=d["proposal"]["content"]["name"], issues=d["issues"], followups=d["followups"],
                             proposal=d["proposal"], evidence=d["evidence"])
            values.append(value)
        (ROOT / ".local/evaluation-example-status.json").write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps([{k: v for k, v in value.items() if k not in {"proposal", "evidence"}} for value in values], ensure_ascii=False))
        return
    if "--analyse" in sys.argv:
        tail = """SELECT recipe,count(*) AS wafers,sum(paired_sites) AS paired_sites,
          avg(inline_mean) AS inline_mean,avg(epm_mean) AS epm_mean,corr(inline_mean,epm_mean) AS pearson,
          min(first_inline) AS first_inline,max(last_epm) AS last_epm
          FROM matched GROUP BY recipe ORDER BY recipe"""
        first = paired_sql("oper A", "oper A CD", "PARA1")
        second = paired_sql("oper B", "oper B CD", "PARA3")
        lot_tail = """SELECT recipe,CASE WHEN lot_id='XXXB7' THEN 'XXXB7' ELSE 'other_XXXB' END AS cohort,
          count(*) AS wafers,avg(inline_mean) AS inline_mean,avg(epm_mean) AS epm_mean,
          min(first_inline) AS first_inline,max(last_epm) AS last_epm
          FROM matched WHERE lot_cd='XXXB' GROUP BY recipe,cohort ORDER BY recipe,cohort"""
        analyses = {"recipe_a": preview(sources["inline_data"], first + tail),
                    "recipe_b": preview(sources["inline_data"], second + tail),
                    "same_recipe_lot": preview(sources["epm_data"], first + lot_tail),
                    "queries": {"recipe_a": first + tail, "recipe_b": second + tail, "same_recipe_lot": first + lot_tail}}
        (ROOT / ".local/evaluation-example-observations.json").write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in analyses.items() if k != "queries"}, ensure_ascii=False))
        return
    metadata = {
        "history": preview(sources["wafer_his"], "SELECT operation, recipe, count(*) AS events FROM public.wafer_his GROUP BY operation, recipe ORDER BY operation, recipe"),
        "inline": preview(sources["inline_data"], "SELECT operation, parameter, count(*) AS events FROM public.inline_data GROUP BY operation, parameter ORDER BY operation, parameter"),
        "epm": preview(sources["epm_data"], "SELECT parameter, count(*) AS events, min(end_dt_tm) AS first_at, max(end_dt_tm) AS last_at FROM public.epm_data GROUP BY parameter ORDER BY parameter"),
        "history_columns": next(row["columns"] for row in bootstrap["catalog"] if row["table_name"] == "wafer_his"),
    }
    (ROOT / ".local/evaluation-example-source.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
