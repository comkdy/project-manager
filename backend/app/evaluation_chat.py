"""Reported evaluation evidence and reviewable next steps; never measurement claims.

Case facts remain authoritative. Optional model interpretations have checked
citations and a pinned evidence digest; history revalidates them before replay.
"""
from __future__ import annotations

from . import db as database


CASE_NOTICE = "과거 평가에서 보고된 관측입니다. 현재 데이터의 재검증 결과나 확정된 원인으로 해석하지 않습니다."
COMPUTED_NOTICE = "승인된 분석 실행에서 계산한 관측입니다. 표시된 조회 시점·데이터 버전의 결과이며, 이번 질문에서 다시 계산한 결과나 확정된 원인은 아닙니다."
SCOPE_NOTICE = "관련 사례라도 현재 카드 필터·조회 기간과 과거 평가 조건이 같다는 뜻은 아닙니다. 적용 조건을 확인한 뒤 비교해야 합니다."


def search_request(request):
    card = request.card.model_dump(mode="json") if request.card else None
    if card:
        # No scripts or large chart configuration are needed for case retrieval.
        card = {key: card.get(key) for key in ("dataset_id", "dataset_version", "title", "x", "y", "color", "trellis", "filters", "period")}
    return {"query": request.message, "project_id": request.project_id,
            "dataset_id": request.dataset_id or (card.get("dataset_id") if card else None),
            "card": card,
            "period_start": request.period_start.model_dump(mode="json") if request.period_start else None}


def retrieve(search):
    from .semantic.evaluations import retrieve_evaluation_cases
    with database.SessionLocal() as db:
        return retrieve_evaluation_cases(db, search["query"], project_id=search.get("project_id"),
            dataset_id=search.get("dataset_id"), card=search.get("card"), limit=5)


def suggestions_for(case):
    details = case.get("details") or {}
    observations = [(label, details.get(key, "")) for label, key in
                    (("공정", "operation"), ("Recipe 평가", "recipe_changes"),
                     ("Inline 관측", "inline_observations"), ("EPM 관측", "epm_observations"),
                     ("승인된 계산 결과", "analysis_result"))]
    reported = "\n".join(f"{label}: {value[:650]}" for label, value in observations if value)
    provenance = f"참고한 과거 평가 사례: {case['name'][:200]} (버전 {case['version']}).\n{reported}\n"
    scope = ("위 내용은 과거 보고이며 현재 결과가 아닙니다. 현재 선택한 데이터셋·카드 필터 범위에서 "
             "사용 가능한 실제 컬럼만 사용하고, 평가 기간·비교군·연결 기준이 없으면 먼저 확인해 주세요. "
             "과거 사례의 조건을 현재 조건으로 자동 적용하지 마세요.")
    ref = {"id": case["id"], "version": case["version"]}
    values = [{"id": "recheck", "title": "같은 변화가 재현되는지 비교",
               "reason": "사례에서 보고된 항목과 평가 조건을 현재 데이터에서 비교할 준비를 합니다.",
               "question": "이 평가 사례의 변화 방향을 재확인할 비교 데이터셋을 만들어줘.\n" + provenance + scope,
               "action": "비교 데이터셋 요청"},
              {"id": "conditions", "title": "장비·기간별로 나누어 확인",
               "reason": "전체에서 보인 관계가 특정 장비나 기간에만 나타나는지 구별할 비교입니다. 장비 정보가 있어야 합니다.",
               "question": "이 평가 사례에 등장하는 측정 항목을 장비·기간별로 나누어 비교할 대시보드 카드를 제안해줘.\n" + provenance + scope,
               "action": "비교 카드 요청"}]
    if details.get("inline_observations") and details.get("epm_observations"):
        values.append({"id": "exceptions", "title": "두 특성이 다르게 움직인 경우 확인",
            "reason": "보고된 Inline·EPM 관계와 일치하지 않는 경우도 찾아 기존 설명의 적용 범위를 확인합니다.",
            "question": "이 평가 사례의 Inline·EPM을 같은 wafer와 적절한 측정 시점으로 대응시켜, 다른 방향으로 움직인 경우를 검토할 조회 SQL을 제안해줘.\n" + provenance + scope,
            "action": "대응 데이터 조회 요청"})
    elif details.get("analysis_result") and case.get("execution_evidence"):
        values.append({"id": "exceptions", "title": "계산한 관계의 적용 범위 확인",
            "reason": "승인된 계산에서 비교한 항목을 현재 데이터에서도 확인합니다. 관계가 없거나 반대로 나타나는 결과도 함께 검토합니다.",
            "question": "승인된 분석 결과의 측정 항목을 현재 데이터에서 다시 비교할 조회 SQL을 제안해줘. 계산에 쓴 컬럼과 그룹·대응 기준부터 확인해줘.\n" + provenance + scope,
            "action": "대응 데이터 조회 요청"})
    return [{**value, "id": f"{case['id']}:{value['id']}", "case_ref": ref} for value in values]


def answer_from(result, search, *, allowed_refs=None):
    cases = result.get("cases", [])
    if allowed_refs is not None:
        cases = [case for case in cases if (case["id"], case["version"]) in allowed_refs]
    if cases:
        explanation = f"현재 데이터와 관련된 평가 사례 {len(cases)}개를 찾았습니다. 등록된 관측과 조건을 확인한 뒤 추가 비교를 제안합니다."
    elif allowed_refs:
        explanation = "참고했던 평가 사례의 승인 상태·데이터 범위가 변경되어 이전 근거와 제안을 표시할 수 없습니다. 현재 조건으로 다시 찾아 주세요."
    else:
        explanation = "현재 데이터와 질문에 맞는 승인 평가 사례를 찾지 못했습니다. AI 지식에 사례를 등록하고 질문·승인 절차를 완료하면 다시 찾아볼 수 있습니다."
    notices = ([CASE_NOTICE] if not cases or any(not case.get("execution_evidence") for case in cases) else [])
    if any(case.get("execution_evidence") for case in cases):
        notices.append(COMPUTED_NOTICE)
    warnings = list(dict.fromkeys([*result.get("warnings", []), *notices, SCOPE_NOTICE]))
    if allowed_refs and len(cases) != len(allowed_refs) and cases:
        warnings.append("일부 이전 근거는 현재 사용할 수 없어 제외했습니다.")
    return {"kind": "evaluation_cases", "status": "ready" if cases else "unavailable" if allowed_refs else "empty",
            "explanation": explanation, "cases": cases, "suggestions": [s for case in cases[:3] for s in suggestions_for(case)],
            "warnings": warnings, "context": result.get("context", {}), "search": search,
            "case_refs": [{"id": case["id"], "version": case["version"]} for case in cases]}


def evaluation_answer(request):
    search = search_request(request)
    result = retrieve(search)
    return answer_from(result, search)


def compact_evaluation_answer(value):
    result = {"kind": "evaluation_cases", "search": value.get("search", {}), "case_refs": value.get("case_refs", [])}
    if value.get('_narrative_record'):
        result['narrative_record'] = value['_narrative_record']
    elif value.get('narrative', {}).get('status') == 'unavailable':
        result['narrative_unavailable'] = True
    return result


def restore_evaluation_answer(value, project_id):
    search = value.get("search", {})
    refs = {(ref["id"], ref["version"]) for ref in value.get("case_refs", [])}
    if search.get("project_id") != project_id or not search.get("query"):
        return {"kind": "evaluation_cases", "status": "unavailable", "explanation": "이전 평가 사례의 조회 범위를 확인할 수 없습니다. 다시 요청하세요.",
                "cases": [], "suggestions": [], "warnings": []}
    try:
        result = retrieve(search)
    except Exception:
        # A withdrawn dataset/source must not prevent the rest of a conversation opening.
        result = {"cases": [], "warnings": ["이전 데이터 범위를 현재 조회할 수 없습니다."], "context": {"project_id": project_id}}
    answer = answer_from(result, search, allowed_refs=refs)
    if value.get('narrative_record'):
        from .evaluation_narrative import restore_narrative
        restore_narrative(answer, value['narrative_record'])
    elif value.get('narrative_unavailable'):
        from .evaluation_narrative import UNAVAILABLE
        answer['narrative'] = {'status': 'unavailable', 'message': UNAVAILABLE}
    return answer
