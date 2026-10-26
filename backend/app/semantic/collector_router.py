from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select

from .. import db as database
from ..auth import get_current_user, require_admin
from ..models import Audit, User, utcnow
from . import collector_config as llm
from .collector_models import CollectedDraft, CollectionSettings, KnowledgeAnswer, KnowledgeQuestion
from .collector_schemas import AnswerInput, CollectionConfigInput, DeferInput, DraftReview, DraftUpdate, EvaluationInput, QuestionCandidate
from .collector_targets import column_options, display_targets, question_presentation, validated_targets
from .collector_service import (audit, conflicts, draft_issues, ensure_settings, future, generate_questions,
                                get_question, inventory, process_answer, question_fingerprint, question_visible, revision)
from .models import KnowledgeItem, KnowledgeVersion
from .evaluation_context import is_case_context, make_case_context, public_case_context, dataset_changed
from .schemas import CreateItem
from .service import catalog_snapshot, definition_issues, digest, normalized, scoped_item

router = APIRouter(prefix="/api/semantic/collection", tags=["knowledge collection"])


def question_json(db, question, *, details=False, datasets=None):
    result = {k: getattr(question, k) for k in ("id", "title", "reason", "kind", "topic", "subject", "status",
                                               "revision", "deferred_until", "created_at")}
    result["dataset"] = None if is_case_context(question) else {k: question.dataset[k] for k in ("id", "name", "version", "project_id")}
    result["case_context"] = public_case_context(question)
    from ..evaluation_recommendation import question_origin
    result['analysis_setup'] = question_origin(db, question)
    from ..evaluation_datasets import question_origin as dataset_origin
    result['dataset_setup'] = dataset_origin(db, question)
    result.update(question_presentation(question))
    result["dataset_changed"] = dataset_changed(question, datasets if datasets is not None else inventory(db))
    latest = db.scalar(select(CollectedDraft).where(CollectedDraft.question_id == question.id)
                       .order_by(CollectedDraft.created_at.desc(), CollectedDraft.id.desc()).limit(1))
    result["followups"] = latest.followups if latest and latest.status == "pending" else []
    if details:
        result["review_feedback"] = latest.review_note if latest and latest.status == "rejected" else ""
        # Identifiers only: draft contents/review remain behind the admin API.
        result["draft"] = ({key: getattr(latest, key) for key in
            ("id", "question_id", "answer_id", "status", "revision", "item_id", "item_version")} if latest else None)
        if result["dataset"] is not None:
            result["dataset"]["target_columns"] = column_options(question.dataset)
        if latest and question.kind == "evaluation":
            from .evaluation_terms import public_terms
            content = latest.proposal["content"]
            result["interpretation"] = {"description": content["description"], "details": {key: value for key, value in content["details"].items() if key != "terms"},
                "terms": public_terms(db, content["details"].get("terms", [])), "issues": draft_issues(db, latest)}
        answers = db.scalars(select(KnowledgeAnswer).where(KnowledgeAnswer.question_id == question.id)
                             .order_by(KnowledgeAnswer.created_at))
        result["answers"] = [{k: getattr(a, k) for k in ("id", "text", "targets", "user_id", "status", "error", "created_at")} for a in answers]
        for a in result["answers"]:
            a["targets"] = display_targets(question.dataset, a["targets"])
            actor = db.get(User, a["user_id"])
            a["user_name"] = (actor.name or actor.username) if actor else "이전 사용자"
        from .evaluation_results import execution_evidence
        execution = execution_evidence(db, question)
        if execution:
            result["execution_evidence"] = execution
    return result


def draft_json(db, draft):
    result = {k: getattr(draft, k) for k in ("id", "question_id", "answer_id", "proposal", "original_proposal", "evidence",
              "followups", "status", "revision", "review_note", "reviewed_by", "reviewed_at", "item_id", "item_version", "created_at")}
    question = get_question(db, draft.question_id)
    result.update(question=question_json(db, question, details=True), issues=draft_issues(db, draft),
                  conflicts=[] if is_case_context(question) else conflicts(db, draft.proposal))
    return result


def get_draft(db, identity, expected):
    draft = db.get(CollectedDraft, identity)
    if not draft:
        raise HTTPException(404, "초안을 찾을 수 없습니다.")
    question = get_question(db, draft.question_id, lock=True)
    draft = db.scalar(select(CollectedDraft).where(CollectedDraft.id == identity).with_for_update().execution_options(populate_existing=True))
    revision(draft.revision, expected)
    if draft.status != "pending" or question.status != "review":
        raise HTTPException(409, "현재 검토 중인 최신 초안만 처리할 수 있습니다.")
    return draft, question


def answer_budget(db, user):
    # Serialize contribution limits for a user across concurrent requests.
    live = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if not live or not live.active:
        raise HTTPException(403, "활성 사용자만 답변할 수 있습니다.")
    count = db.scalar(select(func.count()).select_from(Audit).where(Audit.user_id == user.id,
                      Audit.action.in_(["semantic.collection.answer", "semantic.collection.retry"]),
                      Audit.created_at > utcnow() - timedelta(hours=1)))
    if count >= 10:
        raise HTTPException(429, "답변 구조화는 사용자별 시간당 10회까지 가능합니다. 잠시 후 다시 시도하세요.")


def process_bound(identity, binding):
    with database.use_source(binding):
        process_answer(identity)


@router.get("/evaluation-datasets")
def evaluation_datasets(user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        return [{k: d[k] for k in ("id", "name", "version", "project_id")} for d in inventory(db).values()]


@router.post("/evaluations", status_code=202)
def register_evaluation(body: EvaluationInput, background: BackgroundTasks, user=Depends(get_current_user)):
    """A contribution follows answer/review and never self-approves."""
    with database.SessionLocal.begin() as db:
        answer_budget(db, user)
        # Serialize duplicate submissions across users without adding a table.
        ensure_settings(db)
        dataset = inventory(db).get(body.dataset_id) if body.dataset_id else make_case_context(db, body.project_id)
        if not dataset:
            raise HTTPException(422, "평가 사례와 연결할 현재 DB의 데이터셋을 선택하세요.")
        fingerprint = digest(["evaluation", database.source_database_key(), body.dataset_id, digest(dataset), normalized(body.text)])
        existing = db.scalar(select(KnowledgeQuestion).where(KnowledgeQuestion.fingerprint == fingerprint))
        if existing:
            entry = db.scalar(select(KnowledgeAnswer).where(KnowledgeAnswer.question_id == existing.id)
                              .order_by(KnowledgeAnswer.created_at, KnowledgeAnswer.id).limit(1))
            return {"question_id": existing.id, "answer_id": entry.id if entry else None, "status": "existing"}
        waiting = db.scalar(select(func.count()).select_from(KnowledgeQuestion).where(
            KnowledgeQuestion.connection_key == database.source_database_key(), KnowledgeQuestion.status != "answered"))
        if waiting >= 100:
            raise HTTPException(422, "미해결 질문이 100개입니다. 기존 질문을 먼저 처리하세요.")
        question = KnowledgeQuestion(connection_key=database.source_database_key(), fingerprint=fingerprint,
            dataset=dataset, targets=([{"kind": "dataset", "column": "", "source_id": "", "value": ""}] if body.dataset_id else []),
            created_by=user.id, kind="evaluation", topic="other", subject="평가 사례",
            title="평가 사례: " + body.text.splitlines()[0][:180],
            reason="등록한 평가의 관측 결과와 적용 조건을 정리하고, 필요한 확인을 거쳐 AI 지식으로 검토합니다.",
            status="structuring")
        db.add(question)
        db.flush()
        entry = KnowledgeAnswer(question_id=question.id, text=body.text, targets=question.targets, user_id=user.id)
        db.add(entry)
        db.flush()
        audit(db, user.id, "question", question.id, kind="evaluation", dataset_id=dataset.get("id"), project_id=dataset.get("project_id"))
        audit(db, user.id, "answer", entry.id, question_id=question.id, kind="evaluation")
        result = {"question_id": question.id, "answer_id": entry.id, "status": "queued"}
    background.add_task(process_bound, result["answer_id"], database.current_source())
    return result


@router.get("/settings")
def settings(user=Depends(require_admin)):
    with database.SessionLocal() as db:
        cfg = db.get(CollectionSettings, database.source_database_key())
        result = {"revision": 0, "enabled": False, "interval_hours": 24, "max_questions": 5,
                  "next_run": None, "last_run": None, "last_status": "idle", "last_message": ""}
        if cfg:
            result.update({k: getattr(cfg, k) for k in result})
        result["configured"] = llm.configured()
        result["scope"] = "all_datasets"
        result["datasets"] = [{**{k: d[k] for k in ("id", "version", "name", "project_id")},
                               "target_columns": column_options(d)} for d in inventory(db).values()]
        return result  # Explicit allowlist: no key, URL, request headers or settings object.


@router.post("/settings")
def save_settings(body: CollectionConfigInput, user=Depends(require_admin)):
    with database.SessionLocal.begin() as db:
        cfg = ensure_settings(db)
        revision(cfg.revision, body.expected_revision)
        if future(cfg.lease_until):
            raise HTTPException(409, "질문 생성이 끝난 뒤 설정을 변경하세요.")
        if body.enabled and not llm.configured():
            raise HTTPException(422, "자동 실행 전에 서버의 공용 LLM 설정을 완료하세요.")
        for k in ("enabled", "interval_hours", "max_questions"):
            setattr(cfg, k, getattr(body, k))
        cfg.dataset_ids = []
        cfg.owner_id, cfg.revision = user.id, cfg.revision + 1
        cfg.next_run = utcnow() + timedelta(hours=cfg.interval_hours) if cfg.enabled else None
        audit(db, user.id, "configure", cfg.connection_key, enabled=cfg.enabled, scope="all_datasets")
    return settings(user)


@router.post("/generate")
def generate(user=Depends(require_admin)):
    return generate_questions(actor_id=user.id)


@router.get("/questions")
def questions(project_id: int | None = None, shared_only: bool = False, user=Depends(get_current_user)):
    if shared_only and project_id is not None:
        raise HTTPException(422, "공통 질문과 프로젝트 필터를 동시에 지정할 수 없습니다.")
    with database.SessionLocal() as db:
        datasets = inventory(db)
        rows = db.scalars(select(KnowledgeQuestion).where(KnowledgeQuestion.connection_key == database.source_database_key())
                          .order_by(KnowledgeQuestion.status == "answered", KnowledgeQuestion.created_at.desc()))
        result = []
        for q in rows:
            # Scope selects questions, while visibility must still include every
            # source referenced by a question (including shared lookup datasets).
            from ..evaluation_recommendation import question_origin
            setup = question_origin(db, q)
            question_project = setup['project_id'] if setup else q.dataset.get('project_id')
            if (project_id is not None or shared_only) and question_project != project_id:
                continue
            if question_visible(q, datasets, db=db):
                result.append(question_json(db, q, datasets=datasets))
                if len(result) >= 300:
                    break
        return result


@router.get("/questions/{identity}")
def question_detail(identity: str, user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        return question_json(db, get_question(db, identity), details=True)


@router.post("/questions", status_code=201)
def create_question(body: QuestionCandidate, user=Depends(require_admin)):
    with database.SessionLocal.begin() as db:
        cfg = ensure_settings(db)
        if future(cfg.lease_until):
            raise HTTPException(409, "질문 생성 완료 후 다시 시도하세요.")
        waiting = db.scalar(select(func.count()).select_from(KnowledgeQuestion).where(
            KnowledgeQuestion.connection_key == database.source_database_key(), KnowledgeQuestion.status != "answered"))
        if waiting >= 100:
            raise HTTPException(422, "미해결 질문이 100개입니다. 기존 질문을 먼저 처리하세요.")
        dataset = inventory(db).get(body.dataset_id)
        if not dataset:
            raise HTTPException(422, "수집 대상 데이터셋을 확인하세요.")
        try:
            targets = validated_targets(dataset, body.targets, title=body.title, subject=body.subject, topic=body.topic)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        fingerprint = question_fingerprint(database.source_database_key(), dataset, body.topic, body.subject, targets)
        if db.scalar(select(KnowledgeQuestion.id).where(KnowledgeQuestion.fingerprint == fingerprint)):
            raise HTTPException(409, "같은 대상과 주제의 질문이 이미 있습니다.")
        question = KnowledgeQuestion(connection_key=database.source_database_key(), fingerprint=fingerprint,
                                     dataset=dataset, targets=targets, created_by=user.id, **body.model_dump(exclude={"dataset_id", "targets"}))
        db.add(question)
        db.flush()
        audit(db, user.id, "question", question.id)
        return question_json(db, question, details=True)


@router.post("/questions/{identity}/answers", status_code=202)
def answer(identity: str, body: AnswerInput, background: BackgroundTasks, user=Depends(get_current_user)):
    with database.SessionLocal.begin() as db:
        answer_budget(db, user)
        question = get_question(db, identity, lock=True)
        revision(question.revision, body.expected_revision)
        if dataset_changed(question, inventory(db)):
            raise HTTPException(409, "질문 생성 후 데이터셋이 변경되었습니다. 최신 데이터셋의 질문에 답변하세요.")
        if question.status in {"answered", "structuring"}:
            raise HTTPException(409, "완료되었거나 구조화 중인 질문입니다.")
        count = db.scalar(select(func.count()).select_from(KnowledgeAnswer).where(KnowledgeAnswer.question_id == identity))
        if count >= 12:
            raise HTTPException(422, "답변이 12개 누적되었습니다. 관리자가 초안을 정리해 주세요.")
        try:
            if is_case_context(question) and body.targets:
                raise ValueError("평가 사례에는 자연어 설명만 입력하세요.")
            targets = [] if is_case_context(question) else validated_targets(question.dataset, body.targets)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        entry = KnowledgeAnswer(question_id=identity, text=body.text, targets=targets, user_id=user.id)
        db.add(entry)
        db.flush()
        question.status, question.revision = "structuring", question.revision + 1
        audit(db, user.id, "answer", entry.id, question_id=identity, targets=targets)
        result = {"answer_id": entry.id, "status": "queued"}
    background.add_task(process_bound, entry.id, database.current_source())
    return result


@router.post("/answers/{identity}/retry", status_code=202)
def retry(identity: str, background: BackgroundTasks, user=Depends(get_current_user)):
    with database.SessionLocal.begin() as db:
        answer_budget(db, user)
        entry = db.scalar(select(KnowledgeAnswer).where(KnowledgeAnswer.id == identity).with_for_update())
        if not entry or (entry.user_id != user.id and user.role != "admin"):
            raise HTTPException(404, "답변을 찾을 수 없습니다.")
        question = get_question(db, entry.question_id, lock=True)
        latest = db.scalar(select(KnowledgeAnswer.id).where(KnowledgeAnswer.question_id == question.id)
                           .order_by(KnowledgeAnswer.created_at.desc()).limit(1))
        if entry.status != "error" or question.status in {"answered", "structuring"} or latest != entry.id:
            raise HTTPException(409, "최신 실패 답변만 다시 구조화할 수 있습니다.")
        entry.status, entry.error = "queued", ""
        question.status, question.revision = "structuring", question.revision + 1
        audit(db, user.id, "retry", entry.id)
    background.add_task(process_bound, identity, database.current_source())
    return {"status": "queued"}


@router.post("/questions/{identity}/defer")
def defer(identity: str, body: DeferInput, user=Depends(get_current_user)):
    with database.SessionLocal.begin() as db:
        question = get_question(db, identity, lock=True)
        revision(question.revision, body.expected_revision)
        if question.status not in {"open", "deferred"}:
            raise HTTPException(409, "미답변 질문만 보류할 수 있습니다.")
        question.status = "deferred" if body.days else "open"
        question.deferred_until = utcnow() + timedelta(days=body.days) if body.days else None
        question.revision += 1
        audit(db, user.id, "defer", question.id, days=body.days)
        return question_json(db, question)


@router.get("/drafts")
def drafts(user=Depends(require_admin)):
    with database.SessionLocal() as db:
        datasets = inventory(db)
        rows = db.execute(select(CollectedDraft, KnowledgeQuestion).join(KnowledgeQuestion)
                          .where(KnowledgeQuestion.connection_key == database.source_database_key(),
                                 CollectedDraft.status == "pending").order_by(CollectedDraft.created_at.desc()).limit(100))
        return [draft_json(db, d) for d, q in rows if question_visible(q, datasets, db=db)]


@router.post("/drafts/{identity}")
def update_draft(identity: str, body: DraftUpdate, user=Depends(require_admin)):
    with database.SessionLocal.begin() as db:
        draft, question = get_draft(db, identity, body.expected_revision)
        if body.proposal.kind != question.kind:
            raise HTTPException(422, "지식 종류는 질문과 일치해야 합니다.")
        previous = draft.proposal
        draft.proposal = body.proposal.model_dump()
        draft.revision += 1
        draft.review_note = body.note
        # Keep the unified request's required action in sync after a reviewer
        # resolves missing definitions. The original proposal remains intact.
        from .evaluation_terms import term_questions
        draft.followups = list(dict.fromkeys([*draft_issues(db, draft), *term_questions(draft.proposal["content"]["details"].get("terms", []))]))[:30]
        audit(db, user.id, "edit", draft.id, note=body.note, revision=draft.revision,
              before=previous, after=draft.proposal)
        return draft_json(db, draft)


@router.get("/drafts/{identity}")
def draft_history(identity: str, user=Depends(require_admin)):
    with database.SessionLocal() as db:
        draft = db.get(CollectedDraft, identity)
        if not draft:
            raise HTTPException(404, "초안을 찾을 수 없습니다.")
        return draft_json(db, draft)


@router.post("/drafts/{identity}/review")
def review_draft(identity: str, body: DraftReview, user=Depends(require_admin)):
    with database.SessionLocal.begin() as db:
        draft, question = get_draft(db, identity, body.expected_revision)
        if body.decision == "reject":
            draft.status, question.status = "rejected", "open"
        else:
            issues = draft_issues(db, draft)
            if dataset_changed(question, inventory(db)):
                issues.append("질문 생성 후 데이터셋 또는 스키마가 변경되었습니다. 새 질문으로 다시 검토하세요.")
            if issues:
                raise HTTPException(422, " / ".join(issues))
            proposal = CreateItem.model_validate(draft.proposal)
            matching = [] if is_case_context(question) else conflicts(db, draft.proposal)
            if body.target_item_id:
                item = scoped_item(db, body.target_item_id, lock=True)
                revision(item.revision, body.target_revision)
                if item.kind != proposal.kind or (item.connection_key is None) != (proposal.scope == "common"):
                    raise HTTPException(422, "기존 지식의 종류와 범위가 일치해야 합니다.")
                if item.head_version != item.published_version:
                    raise HTTPException(409, "기존 지식에 미승인 버전이 있습니다. 먼저 해당 버전을 검토하세요.")
                if item.kind == "evaluation":
                    from .evaluations import evaluation_origin
                    origin = evaluation_origin(db, item.id, item.published_version)
                    if not origin or origin.dataset.get("id") != question.dataset.get("id") or origin.dataset.get("project_id") != question.dataset.get("project_id"):
                        raise HTTPException(422, "평가 사례의 원래 데이터셋·프로젝트와 같은 범위에서 변경을 검토하세요.")
                if any(m["id"] != item.id for m in matching):
                    raise HTTPException(409, "동일 이름·별칭의 다른 지식도 있습니다. 의미를 구분해 주세요.")
                item.head_version += 1
                item.revision += 1
            else:
                if matching:
                    raise HTTPException(409, "같은 이름·별칭의 지식이 있습니다. 기존 지식 변경으로 검토하거나 의미를 구분하세요.")
                item = KnowledgeItem(kind=proposal.kind, connection_key=None if proposal.scope == "common" else database.source_database_key())
                db.add(item)
                db.flush()
            content = proposal.content.for_kind(proposal.kind)
            version = KnowledgeVersion(item_id=item.id, version=item.head_version, content=content, created_by=user.id,
                                       status="approved", reviewed_by=user.id, reviewed_at=utcnow(), review_note=body.note)
            # Recheck references with the actual target identity (self-reference, DB scope).
            issues = definition_issues(db, item, version, catalog_snapshot(db, live=item.kind == "query"))
            if issues:
                raise HTTPException(422, " / ".join(issues))
            db.add(version)
            item.published_version = version.version
            draft.item_id, draft.item_version = item.id, version.version
            draft.status, question.status = "approved", "answered"
        draft.reviewed_by, draft.reviewed_at, draft.review_note = user.id, utcnow(), body.note
        draft.revision, question.revision = draft.revision + 1, question.revision + 1
        audit(db, user.id, "review", draft.id, decision=body.decision, note=body.note,
              question_id=question.id, item_id=draft.item_id, item_version=draft.item_version)
        return {"status": draft.status, "item_id": draft.item_id, "item_version": draft.item_version}
