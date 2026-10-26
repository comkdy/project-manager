"""Turn completed measurements into reviewable knowledge without model-written numbers."""
from __future__ import annotations

import json

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import func, select

from .. import db as database
from ..analytics.models import AnalysisRun
from ..models import User
from .collector_models import CollectedDraft, KnowledgeAnswer, KnowledgeQuestion
from .schemas import Content, CreateItem, Strict
from .service import digest

PREFIX = "evaluation-run:"
LIMITATION = "선택한 데이터셋 행의 관측 통계입니다. 독립 실험 수·통계적 유의성·인과관계를 확정하지 않습니다."


class SubmitEvaluationReview(Strict):
    expected_revision: int = Field(ge=1)
    workspace_token: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)


def run_id(question):
    return question.subject[len(PREFIX):] if question.kind == "evaluation" and question.subject.startswith(PREFIX) else None


def result_run(db, question):
    """Read shared evidence without granting execution authority to a reader."""
    from .collector_service import inventory
    from ..analytics.data import validate_snapshot
    identity = run_id(question)
    run = db.get(AnalysisRun, identity) if identity else None
    if not run or run.kind != "evaluation" or run.connection_key != database.source_database_key():
        raise HTTPException(409, "분석 결과의 원본 실행을 확인할 수 없습니다.")
    if run.status != "completed" or not run.result or not run.result.get("summary"):
        raise HTTPException(409, "완료된 분석 결과만 지식으로 검토할 수 있습니다.")
    snapshot = run.request.get("_evaluation_dataset")
    current = inventory(db).get(question.dataset["id"])
    from ..source_compatibility import dataset_compatible
    if not snapshot or digest(snapshot) != digest(question.dataset) or not dataset_compatible(snapshot, current):
        raise HTTPException(409, "분석 후 데이터셋·스키마가 변경되었습니다. 현재 데이터로 다시 실행하세요.")
    validate_snapshot(db, run.source_snapshot, allow_column_additions=True)
    from ..evaluation_analysis import check_scope
    check_scope(db, run)
    return run


def execution_evidence(db, question):
    from ..measurement_items import context_for_run
    if not run_id(question):
        return None
    try:
        run = result_run(db, question)
    except HTTPException as exc:
        return {"run_id": run_id(question), "unavailable": True, "summary": str(exc.detail)}
    case = run.request.get("_evaluation_case")
    from .evaluation_claims import computed_findings
    identities = context_for_run(run.request)
    return {"run_id": run.id, "method_version": run.result.get("method_version"),
            "captured_at": run.result.get("captured_at"), "summary": run.result["summary"],
            "case_ref": {"id": case["id"], "version": case["version"]} if case else None,
            "origin": 'case' if case else 'chat',
            "dataset_version": question.dataset["version"], 'measurement_items': identities,
            'findings': computed_findings(run, identities)}


def review_state(db, run):
    link = run.request.get("_evaluation_review")
    if not link:
        return None
    question = db.get(KnowledgeQuestion, link["question_id"])
    if not question:
        return None
    draft = db.scalar(select(CollectedDraft).where(CollectedDraft.question_id == question.id)
        .order_by(CollectedDraft.created_at.desc(), CollectedDraft.id.desc()).limit(1))
    return {"question_id": question.id, "draft_id": draft.id if draft else None,
            "status": draft.status if draft else question.status,
            "item_id": draft.item_id if draft else None, "item_version": draft.item_version if draft else None}


def result_content(run):
    case = run.request.get("_evaluation_case")
    dataset = run.request["_evaluation_dataset"]
    summary = run.result["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 6000:
        raise HTTPException(422, "분석 결과 요약을 확인할 수 없습니다.")
    # Recipe names are historical context, never evidence of the current selection.
    conditions = json.dumps({"dataset": {k: dataset[k] for k in ("id", "version", "name", "project_id")},
        "settings": run.request.get("settings"),
        "filters": (run.request.get("_evaluation_search", {}).get("card") or {}).get("filters", []),
        "applied_period": run.request.get("_evaluation_scope", {}).get("period"),
        "captured_at": run.result.get("captured_at")}, ensure_ascii=False, sort_keys=True)
    # A large filter set remains available in the immutable run; do not publish
    # a misleading truncated condition as if it were the full scope.
    if len(conditions) > 1900:
        conditions = (f"데이터셋 {dataset['name']} v{dataset['version']}; 조회시각 {run.result.get('captured_at')}. "
                      f"전체 적용 조건은 원본 실행 {run.id}의 설정·필터를 확인하세요.")
    operation = ("현재 데이터셋의 명시한 항목 비교. 참고 사례의 공정: " + case.get("details", {}).get("operation", "미기재")[:850]) if case else '챗 질문에 따라 현재 데이터셋의 항목을 계산했습니다.'
    evidence = f"분석 실행 {run.id}; 계산 방법 {run.result.get('method_version')}"
    if case:
        evidence += f"; 참고 사례 {case['id']} v{case['version']}"
    else:
        evidence += '; 원래 질문: ' + run.request['_chat_analysis']['question'][:800]
    details = {"operation": operation,
        "analysis_result": summary, "conditions": conditions,
        "evidence": evidence,
        "limitations": LIMITATION}
    content = Content(name=(f"현재 데이터 분석: {dataset['name'][:100]} · {run.id[:8]}"),
        description="현재 데이터에서 계산한 결과입니다. 조회 시점과 조건 범위에서 검토하며 과거 사례의 재현·원인으로 자동 확정하지 않습니다.",
        references=[{"item_id": case["id"], "version": case["version"]}] if case else [], details=details)
    proposal = CreateItem(kind="evaluation", scope="database", content=content)
    return {"kind": "evaluation", "scope": "database", "content": proposal.content.for_kind("evaluation")}


def make_draft(db, question, answer, run):
    proposal = result_content(run)
    for old in db.scalars(select(CollectedDraft).where(CollectedDraft.question_id == question.id,
                                                     CollectedDraft.status == "pending")):
        old.status, old.revision = "superseded", old.revision + 1
    # Original measurement text is a separate immutable answer. User follow-up
    # answers are review notes, never new calculation evidence.
    original = db.scalar(select(KnowledgeAnswer).where(KnowledgeAnswer.question_id == question.id)
                         .order_by(KnowledgeAnswer.created_at, KnowledgeAnswer.id).limit(1))
    source_answer = original or answer
    summary = run.result["summary"]
    draft = CollectedDraft(question_id=question.id, answer_id=answer.id, proposal=proposal,
        original_proposal=proposal, evidence={"details.analysis_result": {
            "value": summary, "quote": summary, "answer_id": source_answer.id}}, followups=[])
    db.add(draft)
    db.flush()
    question.status, question.revision = "review", question.revision + 1
    answer.status, answer.error, answer.lease_token, answer.lease_until = "ready", "", None, None
    run.request = {**run.request, "_evaluation_review": {"question_id": question.id, "draft_id": draft.id}}
    return draft


def submit_review(identity, body, user):
    from ..evaluation_analysis import checked_run
    from .collector_service import audit
    with database.SessionLocal.begin() as db:
        run = checked_run(db, identity, user, lock=True)
        if body.workspace_token != database.workspace_token():
            raise HTTPException(409, "분석 범위가 변경되었습니다. 결과를 다시 불러오세요.")
        existing = review_state(db, run)
        if existing:
            return existing
        if body.expected_revision != run.revision:
            raise HTTPException(409, "분석 상태가 갱신되었습니다. 결과를 다시 불러오세요.")
        if (run.status != "completed" or not run.result or not run.result.get("row_count")
                or run.result.get("knowledge_eligible") is False):
            raise HTTPException(422, "관측값이 있는 완료된 분석만 검토 요청할 수 있습니다.")
        proposal = result_content(run)
        dataset = run.request["_evaluation_dataset"]
        question = KnowledgeQuestion(connection_key=run.connection_key,
            fingerprint=digest([PREFIX, run.id]), dataset=dataset,
            targets=[{"kind": "dataset", "column": "", "source_id": "", "value": ""}],
            created_by=user.id, kind="evaluation", topic="other", subject=PREFIX + run.id,
            title=proposal["content"]["name"],
            reason="현재 데이터의 계산 결과를 검토합니다. 수치는 원본 실행에 고정되어 있습니다. 보완 답변은 검토 메모로 남습니다.",
            status="review")
        db.add(question)
        db.flush()
        text = run.result["summary"]
        if body.note:
            text += "\n\n[검토 요청자의 메모 — 계산 근거와 구분]\n" + body.note
        answer = KnowledgeAnswer(question_id=question.id, text=text, targets=question.targets,
                                 user_id=user.id, status="ready")
        db.add(answer)
        db.flush()
        draft = make_draft(db, question, answer, run)
        run.revision += 1
        audit(db, user.id, "evaluation_result", draft.id, run_id=run.id, question_id=question.id)
        return review_state(db, run)


def auto_submit_review(db, run, user):
    """Same immutable result draft as manual submission, in the watch transaction."""
    from .collector_service import audit
    existing = review_state(db, run)
    if existing:
        return existing
    if run.status != 'completed' or not run.result or not run.result.get('knowledge_eligible'):
        return None
    waiting = db.scalar(select(func.count()).select_from(KnowledgeQuestion).where(
        KnowledgeQuestion.connection_key == run.connection_key, KnowledgeQuestion.status != 'answered'))
    if waiting >= 100:
        raise HTTPException(429, '기존 확인 요청을 처리하면 결과 검토 요청을 다시 시도합니다.')
    proposal = result_content(run)
    dataset = run.request['_evaluation_dataset']
    question = KnowledgeQuestion(connection_key=run.connection_key, fingerprint=digest([PREFIX, run.id]),
        dataset=dataset, targets=[{'kind': 'dataset', 'column': '', 'source_id': '', 'value': ''}],
        created_by=user.id, kind='evaluation', topic='other', subject=PREFIX + run.id,
        title=proposal['content']['name'], reason='새 데이터에서 분석한 결과입니다. 해석과 계산 근거를 검토한 뒤 승인하세요.', status='review')
    db.add(question)
    db.flush()
    answer = KnowledgeAnswer(question_id=question.id, text=run.result['summary'], targets=question.targets,
                             user_id=user.id, status='ready')
    db.add(answer)
    db.flush()
    draft = make_draft(db, question, answer, run)
    run.revision += 1
    audit(db, user.id, 'evaluation_result', draft.id, run_id=run.id, question_id=question.id)
    return review_state(db, run)


def result_draft_issues(db, draft):
    question = db.get(KnowledgeQuestion, draft.question_id)
    if not question or not run_id(question):
        if draft.proposal.get("content", {}).get("details", {}).get("analysis_result"):
            return ["계산 결과는 완료된 분석의 검토 요청을 통해서만 등록하세요."]
        return []
    try:
        run = result_run(db, question)
        expected = result_content(run)
        actual = draft.proposal
        if (actual.get("kind") != "evaluation" or actual.get("scope") != "database" or
                actual.get("content", {}).get("details") != expected["content"]["details"] or
                actual.get("content", {}).get("references") != expected["content"]["references"] or
                actual.get("content", {}).get("description") != expected["content"]["description"]):
            return ["계산 결과·조건·근거는 원본 실행과 같아야 합니다. 의견은 검토 메모로 남기고 조건 변경은 새 분석으로 실행하세요."]
    except HTTPException as exc:
        return [str(exc.detail)]
    return []


def computed_content_issues(db, item_id, content):
    from .evaluations import evaluation_origin
    from .models import KnowledgeItem
    item = db.get(KnowledgeItem, item_id)
    origin = evaluation_origin(db, item_id, item.published_version or item.head_version) if item else None
    if not origin or not run_id(origin):
        return ["계산 결과는 완료된 분석에서 등록하세요."] if content.get("details", {}).get("analysis_result") else []
    try:
        expected = result_content(result_run(db, origin))["content"]
        if any(content.get(key) != expected[key] for key in ("details", "references", "description")):
            return ["계산 결과·조건·근거는 원본 실행에서 유지합니다. 다른 조건은 새 분석으로 검토하세요."]
    except HTTPException as exc:
        return [str(exc.detail)]
    return []


def process_result_answer(identity):
    from .collector_service import audit, get_question
    with database.SessionLocal.begin() as db:
        answer = db.get(KnowledgeAnswer, identity)
        question = db.get(KnowledgeQuestion, answer.question_id) if answer else None
        if not question or not run_id(question):
            return False
        if answer.status == "ready":
            return True
        question = get_question(db, question.id, lock=True)
        try:
            actor = db.get(User, answer.user_id)
            if not actor or not actor.active:
                raise HTTPException(403, "답변자의 활성 계정을 확인하세요.")
            run = result_run(db, question)
            if not db.scalar(select(CollectedDraft.id).where(CollectedDraft.answer_id == answer.id)):
                make_draft(db, question, answer, run)
                audit(db, actor.id, "evaluation_result_note", answer.id, run_id=run.id, question_id=question.id)
        except HTTPException as exc:
            answer.status, answer.error = "error", str(exc.detail)
            question.status, question.revision = "open", question.revision + 1
        return True
