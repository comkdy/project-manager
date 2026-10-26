"""Server-owned evidence for approved knowledge supplied to SQL/card skills."""
from typing import Literal
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import Field

from .schemas import StrictModel


class KnowledgeDefinitionRef(StrictModel):
    item_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)


class KnowledgeSource(KnowledgeDefinitionRef):
    name: str
    kind: Literal['term', 'metric', 'data', 'relation', 'query']
    domain: str
    binding_version: int | None


class KnowledgeChoice(KnowledgeDefinitionRef):
    name: str
    domain: str


class KnowledgeClarification(StrictModel):
    id: str
    question: str
    options: list[KnowledgeChoice]


class ChatKnowledge(StrictModel):
    context_id: str
    resolution: Literal['resolved', 'needs_clarification', 'missing_knowledge']
    availability: Literal['available', 'unavailable'] = 'available'
    blocking: bool = False
    sources: list[KnowledgeSource] = Field(default_factory=list)
    clarifications: list[KnowledgeClarification] = Field(default_factory=list)
    selected_definitions: list[KnowledgeDefinitionRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


def metadata(payload):
    """Only app-owned context entries may become displayed source/version claims."""
    return ChatKnowledge.model_validate({
        'context_id': payload['context_id'], 'resolution': payload['resolution'],
        'blocking': bool(payload.get('chat_blocking_gaps', payload.get('blocking_gaps', False))),
        'sources': [{'item_id': e['id'], 'version': e['version'], 'name': e['content']['name'],
                     'kind': e['kind'], 'domain': e['content']['domain'],
                     'binding_version': e['binding']['version'] if e['binding'] else None}
                    for e in payload['items']],
        'clarifications': [{'id': c['id'], 'question': c['reason'], 'options': c['options']}
                           for c in payload['clarifications']],
        'selected_definitions': payload.get('chat_selected_definitions', []),
        'warnings': payload['warnings'], 'gaps': [g['reason'] for g in payload['gaps']],
    }).model_dump(mode='json')


def _context(db, context_id, actor_id, project_id):
    from . import db as database
    from .semantic.models import KnowledgeContext
    record = db.get(KnowledgeContext, context_id)
    if (record is None or record.user_id != actor_id
            or record.connection_key != database.source_database_key() or record.project_id != project_id):
        raise HTTPException(404, '현재 사용자·DB·프로젝트의 지식 근거를 찾을 수 없습니다.')
    if record.payload.get('workspace_token') != database.workspace_token():
        raise HTTPException(409, 'DB 설정이 변경되었습니다. 현재 DB에서 지식을 다시 확인하세요.')
    return record


def _validate(db, payload, catalog, *, lock=False):
    from . import db as database
    from .semantic.models import BindingHead, BindingVersion, KnowledgeItem, KnowledgeVersion
    from .semantic.service import binding_issues, definition_issues
    keys = {(e['id'], e['version']) for e in payload['items']}
    for entry in sorted(payload['items'], key=lambda e: (e['id'], e['version'])):
        item = db.get(KnowledgeItem, entry['id'])
        version = db.get(KnowledgeVersion, (entry['id'], entry['version']),
                         with_for_update={'read': True} if lock else None)
        if (item is None or item.connection_key not in {None, database.source_database_key()}
                or version is None or version.status != 'approved'):
            raise HTTPException(409, '제안에 제공한 업무 지식이 변경되거나 철회되었습니다. 다시 요청하세요.')
        head = db.get(BindingHead, (item.id, version.version, database.source_database_key()))
        binding = (db.get(BindingVersion, (item.id, version.version, database.source_database_key(), entry['binding']['version']),
                          with_for_update={'read': True} if lock else None) if entry['binding'] else None)
        if ((entry['binding'] and (binding is None or binding.status != 'approved'))
                or (not entry['binding'] and head is not None)
                or definition_issues(db, item, version, catalog)
                or binding_issues(item, version, binding, catalog)
                or any((r['item_id'], r['version']) not in keys for r in version.content['references'])):
            raise HTTPException(409, '제안에 제공한 정의·매핑 또는 원천 스키마가 변경되었습니다. 다시 요청하세요.')


def checked_knowledge(context_id, actor_id, project_id, *, db=None, lock=False):
    """Revalidate pinned evidence before executing a preview or persisting a proposal."""
    from . import db as database
    from .semantic.service import catalog_snapshot
    if db is None:
        with database.SessionLocal() as session:
            return checked_knowledge(context_id, actor_id, project_id, db=session, lock=lock)
    record = _context(db, context_id, actor_id, project_id)
    payload = record.payload
    if payload.get('chat_blocking_gaps', payload.get('blocking_gaps', False)):
        raise HTTPException(409, '관련 업무 지식의 정의·매핑을 먼저 확인한 뒤 변경안을 다시 생성하세요.')
    if payload['resolution'] == 'needs_clarification':
        raise HTTPException(409, '업무 정의를 선택한 뒤 변경안을 다시 생성하세요.')
    if payload['items']:
        _validate(db, payload, catalog_snapshot(db, live=True), lock=lock)
    return metadata(payload)


def restore_knowledge(value, actor_id, project_id):
    """History callers must redact the entire old answer when unavailable."""
    from . import db as database
    from .semantic.service import catalog_snapshot
    context_id = value.get('context_id', '')
    try:
        with database.SessionLocal() as db:
            record = _context(db, context_id, actor_id, project_id)
            if record.payload['items']:
                _validate(db, record.payload, catalog_snapshot(db, live=True))
            return metadata(record.payload)
    except HTTPException:
        return ChatKnowledge(context_id=context_id, resolution='missing_knowledge', availability='unavailable',
                             warnings=['당시 제공한 업무 지식을 현재 확인할 수 없습니다. 현재 범위에서 다시 요청하세요.']).model_dump(mode='json')


def retrieve_knowledge(question, actor_id, project_id, *, dataset_id=None,
                       previous_context_id=None, selected_definitions=()):
    from . import db as database
    from .semantic.models import KnowledgeContext
    from .semantic.schemas import ContextRequest, DefinitionRef, SchemaHint
    from .semantic.service import catalog_snapshot, get_semantic_context
    selected = [KnowledgeDefinitionRef.model_validate(ref).model_dump() for ref in selected_definitions]
    if selected and not previous_context_id:
        raise HTTPException(422, '제시된 업무 정의의 컨텍스트와 함께 선택하세요.')
    with database.SessionLocal.begin() as db:
        catalog = catalog_snapshot(db, live=True)
        if previous_context_id:
            prior = _context(db, previous_context_id, actor_id, project_id)
            _validate(db, prior.payload, catalog)
            allowed = {(e['id'], e['version']) for e in prior.payload['items']}
            if any((r['item_id'], r['version']) not in allowed for r in selected):
                raise HTTPException(422, '현재 제시된 업무 정의 중에서 선택하세요.')
            retained = prior.payload.get('chat_selected_definitions', [])
            chosen = {(r['item_id'], r['version']) for r in selected}
            for group in prior.payload['clarifications']:
                options = {(r['item_id'], r['version']) for r in group['options']}
                if len(chosen & options) > 1:
                    raise HTTPException(422, '같은 업무 용어에서는 정의 하나를 선택하세요.')
                if chosen & options:
                    retained = [r for r in retained if (r['item_id'], r['version']) not in options]
            selected = list({(r['item_id'], r['version']): r for r in [*retained, *selected]}.values())
        payload = get_semantic_context(db, ContextRequest(
            query=question, connection_key=database.source_database_key(), workspace_token=database.workspace_token(),
            project_id=project_id, schema_hint=SchemaHint(dataset_id=dataset_id),
            previous_context_id=previous_context_id,
            selected_definitions=[DefinitionRef(**ref) for ref in selected]), SimpleNamespace(id=actor_id), catalog=catalog)
        # A visible parent with an unavailable dependency must not reach a skill.
        entries = payload['items']
        while True:
            keys = {(e['id'], e['version']) for e in entries}
            safe = [e for e in entries if all((r['item_id'], r['version']) in keys for r in e['content']['references'])]
            if len(safe) == len(entries):
                break
            entries = safe
        payload = {**payload, 'items': entries, 'chat_selected_definitions': selected,
                   'chat_blocking_gaps': bool(payload.get('blocking_gaps')) or len(entries) != len(payload['items'])}
        keys = {(e['id'], e['version']) for e in entries}
        payload['output_constraints'] = [c for c in payload['output_constraints'] if (c['item_id'], c['version']) in keys]
        payload['clarifications'] = [{**c, 'options': [o for o in c['options'] if (o['item_id'], o['version']) in keys]}
                                     for c in payload['clarifications']]
        payload['clarifications'] = [c for c in payload['clarifications'] if len(c['options']) > 1]
        payload['resolution'] = ('needs_clarification' if payload['clarifications'] else
                                 'missing_knowledge' if payload['gaps'] else 'resolved')
        db.get(KnowledgeContext, payload['context_id']).payload = payload
        return {'semantic_context': payload, 'knowledge': metadata(payload)}
