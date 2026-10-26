"""Private, database/project-scoped chat history; no changes to skill execution."""
import asyncio
from datetime import datetime, timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, delete, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from . import db as database
from .auth import get_current_user
from .models import Audit, User, uid, utcnow


class ChatHistoryBase(DeclarativeBase):
    pass


class Conversation(ChatHistoryBase):
    __tablename__ = "app_chat_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Turn(ChatHistoryBase):
    __tablename__ = "app_chat_turns"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("app_chat_conversations.id", ondelete="CASCADE"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="running")
    event: Mapped[str | None] = mapped_column(String(20), nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def migrate_chat_history(engine):
    if engine is not None:
        with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(7126002040)"))
            ChatHistoryBase.metadata.create_all(connection)


def owned(db, identity, user_id, *, lock=False):
    query = select(Conversation).where(Conversation.id == identity, Conversation.owner_id == user_id,
                                     Conversation.connection_key == database.source_database_key())
    item = db.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise HTTPException(404, "현재 DB에서 이 대화를 찾을 수 없습니다.")
    return item


def summary(item):
    return {key: getattr(item, key) for key in ("id", "title", "project_id", "revision", "created_at", "updated_at")}


def expire_turns(db, identity):
    for turn in db.scalars(select(Turn).where(Turn.conversation_id == identity, Turn.status == "running",
                                             Turn.lease_until < utcnow())):
        turn.status, turn.completed_at = "interrupted", utcnow()


def applied_ids(db, user_id, turns):
    ids = {(t.response or {}).get("proposal_id") for t in turns if t.event == "proposal"}
    return set(db.scalars(select(Audit.entity_id).where(Audit.user_id == user_id, Audit.action == "chat.apply",
                                                       Audit.entity_id.in_(ids - {None})))) if ids - {None} else set()


def restored_response(turn, user_id, project_id):
    """Resolve stored evidence against current access before showing or reusing it."""
    response = turn.response or {}
    if turn.event == 'data_analysis':
        from .chat_data_analysis import restore
        return restore(response, user_id, project_id)
    if turn.event == "analysis":
        from .analytics.chat_tools import restore_answer
        return restore_answer(response)
    if turn.event == "analysis_proposal":
        from .analytics.proposals import restore_proposal
        return restore_proposal(response, user_id, project_id)
    if turn.event == "evaluation_cases":
        from .evaluation_chat import restore_evaluation_answer
        restored = restore_evaluation_answer(response, project_id)
        restored["analyses"] = restore_evaluation_analyses(response.get("analysis_refs", []), user_id, project_id)
        return restored
    if response.get("knowledge"):
        from .chat_knowledge import restore_knowledge
        knowledge = restore_knowledge(response["knowledge"], user_id, project_id)
        if knowledge["availability"] == "unavailable":
            # Old SQL and prose may contain the withdrawn or newly hidden definition.
            return {"explanation": "참고한 지식이나 원천의 상태가 변경되어 이전 제안을 표시할 수 없습니다. 현재 조건으로 다시 요청하세요.",
                    "knowledge": knowledge, "unavailable": True}
        return {**response, "knowledge": knowledge}
    return response


def attach_evaluation_analysis(db, conversation_id, sequence, user_id, project_id, case_ref, suggestion_id, run_id):
    """Attach an authoritative prepared execution to its original suggestion turn."""
    conversation = owned(db, conversation_id, user_id, lock=True)
    if conversation.project_id != project_id:
        raise HTTPException(409, "평가 사례를 확인한 대화와 분석 프로젝트가 다릅니다.")
    turn = db.get(Turn, (conversation_id, sequence))
    if turn is None or turn.status != "completed" or turn.event != "evaluation_cases":
        raise HTTPException(409, "이 대화에서 평가 사례 제안을 다시 열어 주세요.")
    response = dict(turn.response or {})
    reference = case_ref.model_dump() if hasattr(case_ref, "model_dump") else dict(case_ref)
    if reference not in response.get("case_refs", []) or suggestion_id not in {
        f"{reference.get('id')}:{suffix}" for suffix in ("recheck", "conditions", "exceptions")
    }:
        raise HTTPException(409, "대화에서 확인한 평가 사례와 실행 제안이 다릅니다.")
    refs = list(response.get("analysis_refs", []))
    if not any(ref.get("run_id") == run_id for ref in refs):
        refs.append({"run_id": run_id})
    turn.response = {**response, "analysis_refs": refs}
    conversation.updated_at = utcnow()


def restore_evaluation_analyses(refs, user_id, project_id):
    """Never treat stored run IDs as permission to expose stale execution results."""
    if not refs:
        return []
    from .evaluation_analysis import checked_run
    values = []
    with database.SessionLocal() as db:
        user = db.get(User, user_id)
        for ref in refs:
            identity = ref.get("run_id")
            if not identity:
                continue
            try:
                if user is None or not user.active:
                    raise HTTPException(403, "현재 사용자 권한으로 분석을 확인할 수 없습니다.")
                run = checked_run(db, identity, user)
                if run.request.get("project_id") != project_id:
                    raise HTTPException(409, "분석 프로젝트가 변경되었습니다.")
                values.append({"id": identity, "status": run.status, "available": True})
            except HTTPException:
                values.append({"id": identity, "status": "unavailable", "available": False,
                               "explanation": "데이터나 평가 사례, 접근 권한이 변경되어 이전 분석을 열 수 없습니다."})
    return values


def confirmed_conditions_message(request):
    """Only call with a request read from a server-owned, confirmed proposal."""
    policy = {"latest_per_site": "마지막 측정만 사용", "all_events": "모든 측정 포함"}.get(request.get("retest_policy"), "미정")
    values = {key: value for key, value in request.items() if not key.startswith("_")}
    return "사용자가 조건 확인 화면에서 명시적으로 확정한 분석 조건 (이후 사용자 요청이 우선):\n" + \
        json.dumps(values, ensure_ascii=False) + "\n재측정: " + policy


def model_history(turns, applied, *, user_id=None, project_id=None):
    messages = []
    for turn in turns:
        if turn.status != "completed" or turn.event not in {"proposal", "clarification", "analysis", "analysis_proposal", "evaluation_cases", "data_analysis"}:
            continue
        response = restored_response(turn, user_id, project_id)
        content = response.get("explanation", response.get("question", ""))
        if turn.event == 'data_analysis':
            content += '\n현재 데이터 계산 상태·조건(지식 승인과 별개): ' + json.dumps({k: response.get(k) for k in ('status', 'dataset', 'settings', 'scope')}, ensure_ascii=False)
        if turn.event == "analysis":
            content = "분석 조건: " + json.dumps(response.get("request", {}), ensure_ascii=False) + "\n" + content
        if turn.event == "evaluation_cases":
            narrative = response.get('narrative') or {}
            if narrative.get('status') == 'ready':
                content += '\n승인 자료에 대한 AI 해석과 아직 실행하지 않은 다음 확인 제안(새 승인 사실이 아님):\n'
                content += json.dumps({key: [s['text'] for s in narrative[key]]
                    for key in ('findings', 'next_checks', 'caveats')}, ensure_ascii=False)
            content += "\n다음은 등록된 평가의 관측(직접 보고 또는 승인된 분석 실행 결과)이며, 이번 질문에서 새로 계산한 결과나 확정 조건이 아닙니다.\n"
            content += json.dumps({"cases": [{key: case.get(key) for key in ("id", "version", "name", "details", "execution_evidence")}
                for case in response.get("cases", [])],
                "suggestions": [{key: suggestion[key] for key in ("title", "reason", "question")}
                    for suggestion in response.get("suggestions", [])[:3]]}, ensure_ascii=False)
        if turn.event == "analysis_proposal":
            if response.get("status") == "confirmed" and response.get("request"):
                messages.append({"role": "user", "content": turn.user_text[:6000]})
                messages.append({"role": "user", "content": confirmed_conditions_message(response["request"])[:6000]})
                messages.append({"role": "assistant", "content": content[:6000]})
                continue
            content = "아직 확정·실행하지 않은 분석 조건 제안입니다.\n" + content
        if turn.event == "proposal" and not response.get("unavailable"):
            definition = {k: response[k] for k in ("kind", "explanation", "dataset", "card") if k in response}
            content += "\n사용자가 이 제안을 적용했습니다." if response.get("proposal_id") in applied else "\n적용 여부는 확인되지 않았습니다."
            content += "\n제안 내용:\n" + json.dumps(definition, ensure_ascii=False)
        messages.extend([{"role": "user", "content": turn.user_text[:6000]},
                         {"role": "assistant", "content": content[:6000]}])
    return messages[-20:]


def begin_turn(request, user_id):
    from .chat_tools import require_project
    require_project(request.project_id)
    with database.SessionLocal.begin() as db:
        if request.conversation_id:
            conversation = owned(db, request.conversation_id, user_id, lock=True)
            if conversation.project_id != request.project_id:
                raise HTTPException(409, "이 대화의 프로젝트로 이동한 뒤 이어서 요청하세요.")
            expire_turns(db, conversation.id)
            db.flush()
            if db.scalar(select(Turn.sequence).where(Turn.conversation_id == conversation.id, Turn.status == "running").limit(1)):
                raise HTTPException(409, "이 대화에서 응답을 생성하고 있습니다. 완료 후 다시 요청하세요.")
            if request.conversation_revision != conversation.revision:
                raise HTTPException(409, "다른 화면에서 대화가 갱신되었습니다. 대화 목록에서 다시 열어 주세요.")
            if not request.knowledge_context_id:
                # A follow-up such as "same definition, by month" may not name
                # the term again. Keep provenance even beyond the history page.
                request.knowledge_context_id = conversation.context.get("knowledge_context_id")
        else:
            conversation = Conversation(owner_id=user_id, connection_key=database.source_database_key(),
                                        project_id=request.project_id, title=" ".join(request.message.split())[:100], revision=0)
            db.add(conversation)
            db.flush()
        turns = list(reversed(list(db.scalars(select(Turn).where(Turn.conversation_id == conversation.id)
                                             .order_by(Turn.sequence.desc()).limit(10)))))
        history = model_history(turns, applied_ids(db, user_id, turns), user_id=user_id, project_id=conversation.project_id)
        conversation.revision += 1
        conversation.updated_at = utcnow()
        conversation.context = request.model_dump(mode="json", include={"project_id", "period_start", "dataset_id", "card", "knowledge_context_id"})
        turn = Turn(conversation_id=conversation.id, sequence=conversation.revision,
                    user_text=request.message, lease_until=utcnow() + timedelta(minutes=15))
        db.add(turn)
        db.flush()
        return summary(conversation), history


def finish_turn(identity, sequence, event=None, value=None):
    # Called only with a turn created for this authenticated request, never a
    # client-supplied response. Preserve a completed response on disconnect.
    with database.SessionLocal.begin() as db:
        turn = db.scalar(select(Turn).where(Turn.conversation_id == identity, Turn.sequence == sequence).with_for_update())
        if turn is None or turn.status != "running":
            return
        if event == "proposal":
            # Retain editable definitions, never raw query/plot result rows.
            value = {k: value[k] for k in ("kind", "explanation", "dataset", "card", "proposal_id",
                      "project_id", "period_start", "workspace_token", "knowledge", "knowledge_context_id") if k in value}
        if event == "analysis_proposal":
            # The authoritative plan is stored separately, not trusted from a replay.
            value = {"kind": "analysis_proposal", "proposal_id": value["proposal_id"]}
        if event == "evaluation_cases":
            from .evaluation_chat import compact_evaluation_answer
            value = compact_evaluation_answer(value)
        if event == 'data_analysis':
            value = {'kind': 'data_analysis', 'run_id': value['run_id']}
        if value is not None and len(json.dumps(value, ensure_ascii=False).encode()) > 256_000:
            event, value = "error", {"message": "응답이 대화 저장 한도를 초과했습니다. 요청 범위를 줄여 주세요."}
        turn.status = "completed" if event in {"proposal", "clarification", "analysis", "analysis_proposal", "evaluation_cases", "data_analysis"} else "failed" if event == "error" else "interrupted"
        turn.event, turn.response, turn.completed_at = event, value, utcnow()
        conversation = db.get(Conversation, identity)
        if conversation:
            conversation.updated_at = utcnow()
            if event in {"proposal", "clarification"} and value and value.get("knowledge", {}).get("context_id"):
                conversation.context = {**conversation.context, "knowledge_context_id": value["knowledge"]["context_id"]}


async def persist_stream(conversation, events, encode):
    try:
        yield encode("conversation", conversation)
        async for block in events:
            event = block.split("\n", 1)[0].removeprefix("event: ")
            if event in {"proposal", "clarification", "analysis", "analysis_proposal", "evaluation_cases", "data_analysis", "error"}:
                value = json.loads(block.split("\ndata: ", 1)[1])
                await asyncio.to_thread(finish_turn, conversation["id"], conversation["revision"], event, value)
            yield block
    finally:
        # Shield persistence when Starlette cancels a disconnected SSE response.
        import anyio
        from starlette.concurrency import run_in_threadpool
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(finish_turn, conversation["id"], conversation["revision"])


router = APIRouter(prefix="/api/chat/conversations", tags=["private chat history"])


@router.get("")
def list_conversations(project_id: int | None = None, search: str = Query(default="", max_length=100),
                       offset: int = Query(default=0, ge=0), user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        query = select(Conversation).where(Conversation.owner_id == user.id,
            Conversation.connection_key == database.source_database_key(), Conversation.project_id == project_id)
        if search.strip():
            query = query.where(Conversation.title.contains(search.strip(), autoescape=True))
        rows = list(db.scalars(query.order_by(Conversation.updated_at.desc(), Conversation.id).offset(offset).limit(31)))
        return {"items": [summary(r) for r in rows[:30]], "next_offset": offset + 30 if len(rows) > 30 else None}


@router.get("/{identity}")
def get_conversation(identity: str, before: int | None = Query(default=None, ge=1), user=Depends(get_current_user)):
    with database.SessionLocal.begin() as db:
        item = owned(db, identity, user.id)
        expire_turns(db, identity)
        db.flush()
        query = select(Turn).where(Turn.conversation_id == identity)
        if before is not None:
            query = query.where(Turn.sequence < before)
        rows = list(db.scalars(query.order_by(Turn.sequence.desc()).limit(31)))
        turns = list(reversed(rows[:30]))
        applied = applied_ids(db, user.id, turns)
        messages = []
        for turn in turns:
            messages.append({"id": f"{turn.sequence}-user", "role": "user", "text": turn.user_text, "created_at": turn.created_at})
            response = restored_response(turn, user.id, item.project_id)
            content = response.get("explanation", response.get("question", response.get("message", "")))
            if turn.status == "interrupted":
                content = "응답이 중단되었습니다. 이어서 요청할 수 있습니다."
            if content:
                message = {"id": f"{turn.sequence}-assistant", "role": "assistant", "text": content,
                           "status": turn.status, "created_at": turn.completed_at}
                if response.get("knowledge"):
                    message["knowledge"] = response["knowledge"]
                if turn.event == "proposal" and not response.get("unavailable"):
                    message.update(proposal=response, applied=response.get("proposal_id") in applied)
                if turn.event == "clarification":
                    message["options"] = response.get("options", [])
                if turn.event == "analysis":
                    message["analysis"] = response
                if turn.event == "analysis_proposal":
                    message["analysis_proposal"] = response
                if turn.event == "evaluation_cases":
                    message["evaluation_cases"] = response
                if turn.event == 'data_analysis':
                    message['data_analysis'] = response
                messages.append(message)
        return {**summary(item), "context": item.context, "messages": messages,
                "running": any(t.status == "running" for t in turns),
                "next_before": turns[0].sequence if len(rows) > 30 else None}


@router.delete("/{identity}", status_code=204)
def delete_conversation(identity: str, user=Depends(get_current_user)):
    with database.SessionLocal.begin() as db:
        item = owned(db, identity, user.id, lock=True)
        expire_turns(db, identity)
        db.flush()
        if db.scalar(select(Turn.sequence).where(Turn.conversation_id == identity, Turn.status == "running").limit(1)):
            raise HTTPException(409, "응답이 끝난 뒤 대화를 삭제하세요.")
        db.execute(delete(Turn).where(Turn.conversation_id == identity))
        db.delete(item)
