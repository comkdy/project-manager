"""Configured LLM transport and authenticated review/apply endpoints."""
from __future__ import annotations

import json
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_editor
from .llm_settings import llm_configuration, load_key, validate_llm_url
from .schemas import CardSpec, ISOWeek
from .chat_contracts import ProposalApply, ProposalPreview
from .chat_knowledge import KnowledgeDefinitionRef
from .chat_tools import (apply_proposal, check_workspace, knowledge_context,
                         preview_proposal, proposal_catalog)
from .chat_workflow import ModelAdapter, run_workflow

router = APIRouter(prefix="/api", tags=["assistant"])


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=6000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    project_id: int | None = None
    period_start: ISOWeek | None = None
    dataset_id: str | None = None
    card: CardSpec | None = None
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    save_history: bool = False
    conversation_id: str | None = Field(default=None, max_length=36)
    conversation_revision: int | None = Field(default=None, ge=1)
    knowledge_context_id: str | None = Field(default=None, max_length=36)
    selected_knowledge: list[KnowledgeDefinitionRef] = Field(default_factory=list, max_length=20)


def selected_card_python_source(card, catalog):
    """Give the card skill editable source without querying measurement rows.

    Dataset-input scripts already carry their complete source on selected_card.
    Simple/legacy cards get a data-driven baseline from their visible catalog
    columns. A stale editor selection must not prevent the user asking for help.
    """
    if card is None or card.kind == 'gantt' or (card.editor_mode == 'python' and card.script_input == 'dataset'):
        return None
    from .card_expressions import NUMERIC_TYPES
    from .plotly_codegen import expanded_dataset_script
    from .plotly_policy import parse_script
    try:
        dataset = next((item for item in catalog if item['id'] == card.dataset_id), None)
        if dataset is None:
            return None
        columns = {column['name']: column['type'] for column in dataset['columns']}
        referenced = [card.x, card.y, card.color, card.trellis, *(f.column for f in card.filters)]
        if card.value_expression:
            referenced += [token.name for token in card.value_expression if token.kind == 'column']
        if any(name and name not in columns for name in referenced):
            return None
        if card.kind != 'kpi' and not card.x:
            return None
        if card.kind == 'wafer' and not card.y:
            return None
        if card.kind in {'scatter', 'box'} and card.value_expression is None and not card.y:
            return None
        if card.aggregate in {'sum', 'avg', 'min', 'max'} and not card.y and card.value_expression is None:
            return None
        if card.kind in {'bar', 'line'} and card.aggregate == 'none' and not card.y and card.value_expression is None:
            return None
        if card.aggregate == 'yield' and not {'tested_dies', 'passed_dies'}.issubset(columns):
            return None
        source, _ = expanded_dataset_script(card.model_dump(), dataset['columns'], numeric_color=card.kind == 'scatter' and columns.get(card.color) in NUMERIC_TYPES)
        parse_script(source)
        return source
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return None


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def proposal_context(request, actor_id=None):
    """Load the SQL/card context only when the classified request needs it."""
    from .db import workspace_token
    from .project_settings import managed_project, project_json
    catalog, sources = proposal_catalog(request, all_datasets=True)
    knowledge = knowledge_context(sources, question=request.message, project_id=request.project_id,
        actor_id=actor_id, dataset_id=request.dataset_id or (request.card.dataset_id if request.card else None),
        previous_context_id=request.knowledge_context_id, selected_definitions=request.selected_knowledge)
    context = {"datasets": catalog, "sources": sources, **knowledge, "project_id": request.project_id,
               "period_start": request.period_start.model_dump() if request.period_start else None,
               "selected_dataset_id": request.dataset_id, "workspace_token": workspace_token(),
               "selected_card": request.card.model_dump(mode="json") if request.card else None}
    python_source = selected_card_python_source(request.card, catalog)
    if python_source is not None:
        context['selected_card_python_source'] = python_source
        context['selected_card_python_input'] = 'dataset'
        if request.card.editor_mode == 'python' and request.card.script_input == 'chart':
            context['selected_card'].update(python_script=python_source, script_input='dataset')
    project = managed_project(request.project_id)
    if project:
        context['project'] = project_json(project)
    if len(json.dumps(context, ensure_ascii=False, default=str)) > 100000:
        raise ValueError("데이터 구조가 너무 큽니다. 사용할 원천과 데이터셋 범위를 줄여 주세요.")
    return context


@router.post("/chat/preview")
def review(body: ProposalPreview, user=Depends(require_editor)):
    check_workspace(body.workspace_token)
    return preview_proposal(body.proposal, body.project_id, period_start=body.period_start, actor_id=user.id)


@router.post("/chat/apply")
def apply(body: ProposalApply, user=Depends(require_editor)):
    return apply_proposal(body, user)


@router.post("/chat")
async def chat(request: ChatRequest, http_request: Request, user=Depends(require_editor)):
    conversation = None
    if request.save_history or request.conversation_id:
        from .chat_history import begin_turn
        conversation, history = await run_in_threadpool(begin_turn, request, user.id)
        request = request.model_copy(update={"history": [ChatMessage(**m) for m in history]})
    async def events():
        base_url, model = llm_configuration()
        if not base_url or not model:
            yield sse("error", {"message": "LLM이 아직 설정되지 않았습니다. 서버의 LLM_BASE_URL과 LLM_MODEL을 설정해 주세요. 수동 편집은 계속 사용할 수 있습니다."})
            yield sse("done", {})
            return
        try:
            from .chat_tools import require_project
            key, _ = await run_in_threadpool(load_key, user.id)
            endpoint = await run_in_threadpool(validate_llm_url, base_url)
            await run_in_threadpool(require_project, request.project_id)
            context = {"project_id": request.project_id, "selected_dataset_id": request.dataset_id,
                       "selected_card": request.card.model_dump(mode="json") if request.card else None}
            headers = {"Authorization": f"Bearer {key}"}
            async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), trust_env=False, follow_redirects=False) as client:
                from .chat_grounding import routing_grounding
                adapter = ModelAdapter(client, endpoint, model, headers, request, context,
                                       context_loader=lambda: proposal_context(request, user.id),
                                       routing_loader=lambda: routing_grounding(request))
                adapter.actor_id = user.id
                adapter.conversation_id = conversation["id"] if conversation else None
                adapter.turn_sequence = conversation["revision"] if conversation else None
                async for event, value in run_workflow(request, adapter):
                    if await http_request.is_disconnected():
                        return
                    if event == "proposal":
                        value["proposal_id"] = str(uuid.uuid4())
                    yield sse(event, value)
        except httpx.TimeoutException:
            yield sse("error", {"message": "LLM 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."})
        except httpx.HTTPError:
            yield sse("error", {"message": "LLM에 연결할 수 없습니다. 서버 주소와 실행 상태를 확인해 주세요."})
        except (ValueError, HTTPException) as exc:
            yield sse("error", {"message": (str(exc.detail) if isinstance(exc, HTTPException) else str(exc))[:2000]})
        except Exception:
            yield sse("error", {"message": "변경안 검증에 실패했습니다. 등록된 데이터셋과 컬럼을 확인해 주세요."})
        yield sse("done", {})
    stream = events()
    if conversation:
        from .chat_history import persist_stream
        stream = persist_stream(conversation, stream, sse)
    return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
