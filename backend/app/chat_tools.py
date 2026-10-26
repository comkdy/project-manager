"""App-owned tools. Models cannot bypass validation or invoke persistence."""
import hashlib
import json
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select, text, update

from . import db as database
from .chat_contracts import DatasetDefinition, ProposalDefinition
from .db import SessionLocal, source_database_key
from .models import Audit, Dashboard, DatabaseMount, DatasetVersion, Relation, Source
from .query import (assistant_catalog, chart_query, clear_cache, dataset_json, load_dataset,
                    preview_dataset, source_json)
from .schemas import CardSpec, DatasetSpec, QueryContext
from .table_visibility import hidden_card_ids, source_visible


TOOL_DESCRIPTIONS = {
    "validate_query": "등록 원천과 읽기 전용 SQL을 검증하거나 단일 테이블 피벗 설정에서 SQL을 생성하고, 표시할 SQL 그대로 제한된 결과를 조회",
    "prepare_dataset": "검증한 SQL과 출력 컬럼으로 저장 전 데이터셋 초안 구성",
    "preview_card": "기존 또는 초안 데이터셋으로 카드 설정을 검증하고 실제 차트 데이터 조회",
    "apply_proposal": "사용자가 적용한 데이터셋·카드를 앱 메타데이터에 원자적으로 저장",
}


def require_project(project_id):
    from .catalog import projects_list
    if project_id is not None and project_id not in {p["id"] for p in projects_list()}:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")


def check_workspace(token):
    if token != database.workspace_token():
        raise HTTPException(409, "DB 설정이 변경되었습니다. 현재 DB에서 다시 요청해 주세요.")


def proposal_catalog(request, *, all_datasets=False):
    require_project(request.project_id)
    selected = assistant_catalog(request.dataset_id, project_id=request.project_id)
    if request.dataset_id and not selected and request.project_id is not None:
        # Saved chats/cards may reference a shared definition. Keep their
        # explicit context readable; it is never a new catalog/reuse candidate.
        # Execution and any newly saved definition use the current project.
        legacy = load_dataset(request.dataset_id)
        if legacy.project_id is None:
            selected = [dataset_json(legacy)]
    if request.dataset_id and not selected:
        raise ValueError("현재 프로젝트에서 사용할 수 없는 데이터셋입니다.")
    catalog = assistant_catalog(project_id=request.project_id) if all_datasets else selected
    if request.card and not any(d["id"] == request.card.dataset_id for d in catalog):
        latest = load_dataset(request.card.dataset_id)
        if latest.project_id is not None and latest.project_id != request.project_id:
            raise ValueError("현재 프로젝트에서 사용할 수 없는 카드입니다.")
        catalog.append(dataset_json(latest))
    with SessionLocal() as db:
        sources = [source_json(s) for s in db.scalars(select(Source).order_by(Source.id)) if source_visible(s)]
    return catalog, sources


def knowledge_context(sources, *, question="", project_id=None, actor_id=None, dataset_id=None,
                      previous_context_id=None, selected_definitions=()):
    """Visible relation hints and validated approved knowledge for SQL/card skills."""
    from .catalog import relation_json
    ids = {s["id"] for s in sources}
    with SessionLocal() as db:
        result = {"relations": [relation_json(r) for r in db.scalars(select(Relation))
                               if r.left_source_id in ids and r.right_source_id in ids]}
    if actor_id is not None:
        from .chat_knowledge import retrieve_knowledge
        result.update(retrieve_knowledge(question, actor_id, project_id, dataset_id=dataset_id,
                                         previous_context_id=previous_context_id, selected_definitions=selected_definitions))
    return result


def validate_query(source_id, sql, name, project_id, pivot=None):
    """Returns schema from the real engine, not model-invented output columns."""
    spec = DatasetSpec(source_id=source_id, raw_sql=sql, pivot=pivot)
    result = preview_dataset(spec, QueryContext(project_id=project_id, limit=30))
    # The shared pivot compiler returns both the validated recipe and its exact
    # SQL. Keep them together through preview, card dependencies and persistence.
    return {"definition": {"name": name, "spec": result["spec"]},
            "columns": result["columns"], "preview": result}


def prepare_dataset(validated, name):
    if not name.strip():
        raise ValueError("데이터셋 이름을 입력하세요.")
    return {"name": name, "spec": validated["definition"]["spec"]}


def find_reusable_dataset(definition, project_id):
    from .dataset_reuse import reusable_dataset
    return reusable_dataset(definition, assistant_catalog(project_id=project_id), project_id)


def preview_card(card, project_id, definition=None, validated=None, *, period_start=None):
    context = QueryContext(project_id=project_id, period_start=period_start, refresh=True)
    if definition is not None:
        definition = DatasetDefinition.model_validate(definition)
        validated = validated or validate_query(definition.spec.source_id, definition.spec.raw_sql,
                                                 definition.name, project_id, definition.spec.pivot)
        draft = SimpleNamespace(id=card.dataset_id, version=card.dataset_version, project_id=project_id,
                                spec=definition.spec, columns=validated["columns"])
        return chart_query(card, context, dataset=draft)
    dataset = load_dataset(card.dataset_id)
    if dataset.project_id is not None and dataset.project_id != project_id:
        raise HTTPException(422, "현재 프로젝트에서 사용할 수 없는 데이터셋입니다.")
    return chart_query(card, context)


def normalize_card_source(card, project_id, definition=None, validated=None):
    """Expose old card transformations in source before preview/review/save."""
    if card.editor_mode != 'python' or card.script_input != 'chart':
        return card
    from .query import validate_card
    from .plotly_codegen import expanded_dataset_script
    from .plotly_scripts import validate_script
    if definition is not None:
        definition = DatasetDefinition.model_validate(definition)
        validated = validated or validate_query(definition.spec.source_id, definition.spec.raw_sql,
                                                 definition.name, project_id, definition.spec.pivot)
        dataset = SimpleNamespace(id=card.dataset_id, version=card.dataset_version, project_id=project_id,
                                  spec=definition.spec, columns=validated['columns'])
    else:
        dataset = load_dataset(card.dataset_id)
        if dataset.project_id is not None and dataset.project_id != project_id:
            raise HTTPException(422, '현재 프로젝트에서 사용할 수 없는 데이터셋입니다.')
    compiled = validate_card(card, dataset)
    script, _ = expanded_dataset_script(card.model_dump(), compiled['columns'])
    validate_script(script)
    return card.model_copy(update={'script_input':'dataset', 'python_script':script})


def preview_proposal(proposal, project_id, *, period_start=None, actor_id=None, knowledge_db=None):
    require_project(project_id)
    proposal = ProposalDefinition.model_validate(proposal)
    result = proposal.model_dump(exclude_none=True)
    if proposal.evaluation_dataset_recommendation_id:
        from .evaluation_datasets import check_proposal
        if knowledge_db is not None:
            check_proposal(knowledge_db, proposal.evaluation_dataset_recommendation_id, actor_id, project_id)
        else:
            with database.SessionLocal() as db:
                check_proposal(db, proposal.evaluation_dataset_recommendation_id, actor_id, project_id)
    if proposal.knowledge_context_id:
        from .chat_knowledge import checked_knowledge
        result['knowledge'] = checked_knowledge(proposal.knowledge_context_id, actor_id, project_id,
                                                 db=knowledge_db, lock=knowledge_db is not None)
    validated = None
    if proposal.dataset:
        ds = proposal.dataset
        validated = validate_query(ds.spec.source_id, ds.spec.raw_sql, ds.name, project_id, ds.spec.pivot)
        result["dataset"] = validated["definition"]
        result["query_result"] = {k: validated["preview"][k] for k in ("columns", "rows", "meta", "next_cursor")}
    if proposal.card:
        definition = proposal.dataset.model_dump() if proposal.dataset else None
        card = normalize_card_source(proposal.card, project_id, definition, validated)
        result['card'] = card.model_dump(exclude_none=True)
        result["card_result"] = preview_card(card, project_id,
                                            definition, validated,
                                            period_start=period_start)
        result["card"]["dataset_version"] = result["card_result"]["meta"]["dataset_version"]
    result.update(project_id=project_id, workspace_token=database.workspace_token(),
                  period_start=QueryContext(period_start=period_start).model_dump()["period_start"])
    return result


def apply_proposal(body, user):
    """No source writes. Conflict/cancel cannot leave an orphan dataset."""
    check_workspace(body.workspace_token)
    require_project(body.project_id)
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json", exclude={"request_id", "dashboard_version"}),
                                            sort_keys=True).encode()).hexdigest()
    action_id = str(body.request_id)
    with SessionLocal() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                   {"key": f"chat:{user.id}:{action_id}"})
        previous = db.scalar(select(Audit).where(Audit.user_id == user.id, Audit.action == "chat.apply",
                                                Audit.entity_id == action_id))
        if previous:
            if previous.details["fingerprint"] != fingerprint:
                raise HTTPException(409, "적용 요청이 변경되었습니다. 새 요청으로 다시 적용하세요.")
            return previous.details["result"]
        # Activation uses this same lock; the reviewed workspace must remain active.
        db.execute(text("SELECT pg_advisory_xact_lock(7126002004)"))
        binding = database.current_source()
        mount = db.get(DatabaseMount, 1)
        if binding.profile_id and (not mount or mount.profile_id != binding.profile_id or mount.revision != binding.revision):
            raise HTTPException(409, "활성 DB가 변경되었습니다. 다시 요청해 주세요.")
        preview = preview_proposal(body.proposal, body.project_id, period_start=body.period_start,
                                   actor_id=user.id, knowledge_db=db)
        proposal = body.proposal
        discovery = None
        if proposal.evaluation_dataset_recommendation_id:
            from .evaluation_datasets import check_proposal
            discovery = check_proposal(db, proposal.evaluation_dataset_recommendation_id, user.id, body.project_id, applying=True)
        result = {}
        board = None
        if proposal.card:
            board = db.get(Dashboard, body.dashboard_id) if body.dashboard_id else None
            if not board or board.connection_key != source_database_key() or board.project_id != body.project_id:
                raise HTTPException(404, "현재 프로젝트의 대시보드를 찾을 수 없습니다.")
            if board.version != body.dashboard_version:
                raise HTTPException(409, "다른 사용자가 대시보드를 수정했습니다. 최신 보드를 확인한 후 적용하세요.")
            if proposal.card.id in hidden_card_ids(db, board.cards):
                raise HTTPException(422, "숨겨진 카드 ID는 사용할 수 없습니다.")
            if len(board.cards) >= 40 and all(c["id"] != proposal.card.id for c in board.cards):
                raise HTTPException(422, "카드는 최대 40개까지 저장할 수 있습니다.")
        card = CardSpec.model_validate(preview["card"]) if proposal.card else None
        if proposal.dataset:
            # The workspace advisory lock above serializes competing chat saves.
            # Re-check after preview so simultaneous proposals cannot proliferate.
            reused = find_reusable_dataset(preview['dataset'], body.project_id) if card else None
            if reused:
                result.update(dataset_id=reused['id'], dataset_name=reused['name'], dataset_reused=True)
                card = card.model_copy(update={'dataset_id':reused['id'], 'dataset_version':reused['version']})
            else:
                dataset = DatasetVersion(id=str(uuid.uuid4()), version=1, project_id=body.project_id,
                                         name=proposal.dataset.name, spec=preview['dataset']['spec'],
                                         columns=preview["query_result"]["columns"])
                db.add(dataset)
                result.update(dataset_id=dataset.id, dataset_name=dataset.name)
                if discovery:
                    db.flush()
                    from .semantic.collector_service import inventory
                    from .semantic.service import digest
                    discovery.dataset_id = dataset.id
                    discovery.result = {**discovery.result, 'status': 'applied', 'proposal': None,
                        'definition_edited': digest(preview['dataset']['spec']) != digest(discovery.result['proposal']['dataset']['spec']),
                        'dataset': {'id': dataset.id, 'name': dataset.name, 'version': 1, 'project_id': body.project_id},
                        'applied_snapshot': digest(inventory(db)[dataset.id])}
                    discovery.revision += 1
                if card:
                    card = card.model_copy(update={"dataset_id": dataset.id, "dataset_version": 1})
        if card:
            data = card.model_dump()
            found = any(c["id"] == card.id for c in board.cards)
            cards = [data if c["id"] == card.id else c for c in board.cards] if found else [*board.cards, data]
            layout = board.layout if found else [*board.layout, {
                "i": card.id, "x": 0, "y": max([p["y"] + p["h"] for p in board.layout] or [0]), "w": 6, "h": 5}]
            changed = db.execute(update(Dashboard).where(Dashboard.id == board.id, Dashboard.version == body.dashboard_version)
                                 .values(cards=cards, layout=layout, version=body.dashboard_version + 1))
            if changed.rowcount != 1:
                raise HTTPException(409, "대시보드가 변경되었습니다. 최신 보드를 확인한 후 적용하세요.")
            result.update(card_id=card.id, dashboard_id=board.id, dashboard_version=body.dashboard_version + 1)
        db.add(Audit(user_id=user.id, action="chat.apply", entity_id=action_id,
                     details={"fingerprint": fingerprint, "result": result}))
        db.commit()
    clear_cache()
    return result
