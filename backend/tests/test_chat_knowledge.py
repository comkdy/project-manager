"""Approved evidence, scoped choices and proposal execution boundaries; no real LLM."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import assistant, chat_tools, chat_workflow, db as database
from app.chat_contracts import ProposalDefinition, Route
from app.chat_knowledge import (ChatKnowledge, KnowledgeDefinitionRef, checked_knowledge,
                                restore_knowledge, retrieve_knowledge)
from app.models import DatasetVersion, Relation, Source
from app.semantic.models import (BindingHead, BindingVersion, KnowledgeContext,
                                 KnowledgeItem, KnowledgeVersion, SemanticBase)
from app.semantic.schemas import BindingContent, Content
from app.semantic.service import capture_binding, catalog_snapshot
from test_assistant_routing_context import events, routing


@pytest.fixture
def knowledge(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    for table in (Source.__table__, Relation.__table__, DatasetVersion.__table__):
        table.create(engine)
    SemanticBase.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(database, 'SessionLocal', sessions)
    monkeypatch.setattr(chat_tools, 'SessionLocal', sessions)
    monkeypatch.setattr(database, 'active_source', database.SourceBinding(None, 'knowledge-db', revision=1, ignored_tables=()))
    monkeypatch.setattr(chat_tools, 'require_project', lambda _: None)
    from app import catalog, source_discovery
    monkeypatch.setattr(catalog, 'projects_list', lambda: [{'id': 1, 'name': 'Project'}])
    physical = [{'schema_name': 'public', 'table_name': 'measurements',
                 'columns': [{'name': 'amount', 'type': 'numeric'}], 'primary_key': 'amount'}]
    monkeypatch.setattr(source_discovery, 'database_tables', lambda **kwargs: physical)
    with sessions.begin() as db:
        db.add(Source(id='measurements', connection_key='knowledge-db', name='Measurements', schema_name='public',
                      table_name='measurements', primary_key='amount', project_column=None, columns=physical[0]['columns']))
        db.add(DatasetVersion(id='dataset', version=1, name='Dataset', project_id=None,
                             spec={'source_id': 'measurements'}, columns=physical[0]['columns']))
    yield SimpleNamespace(sessions=sessions, physical=physical, monkeypatch=monkeypatch)
    engine.dispose()


def add(env, identity, *, name='검사 기준', domain='', status='approved', connection=None,
        mapped=False, references=()):
    content = Content(name=name, domain=domain, description='담당자가 승인한 집계 의미',
                      references=list(references)).for_kind('term')
    with env.sessions.begin() as db:
        db.add(KnowledgeItem(id=identity, kind='term', connection_key=connection,
                             head_version=1, published_version=1 if status == 'approved' else None))
        db.add(KnowledgeVersion(item_id=identity, version=1, content=content, status=status,
                                created_by='owner', reviewed_by='reviewer' if status == 'approved' else None))
        if mapped:
            binding = BindingContent(sources=[{'source_id': 'measurements', 'columns': ['amount']}]).model_dump()
            db.add(BindingHead(item_id=identity, definition_version=1, connection_key='knowledge-db', published_version=1))
            db.add(BindingVersion(item_id=identity, definition_version=1, connection_key='knowledge-db', version=1,
                                  content=binding, schema_snapshot=capture_binding(binding, catalog_snapshot(db)),
                                  status='approved', created_by='owner'))
    return {'item_id': identity, 'version': 1}


def retrieve(**kwargs):
    return retrieve_knowledge(kwargs.pop('question', '검사 기준'), kwargs.pop('actor_id', 'test'),
                              kwargs.pop('project_id', None), **kwargs)


def test_only_current_approved_knowledge_and_dependencies_reach_skills(knowledge):
    dependency = add(knowledge, 'dependency', name='원천 의미', mapped=True)
    add(knowledge, 'approved', references=[dependency])
    add(knowledge, 'draft', status='draft')
    add(knowledge, 'foreign', connection='other-db')
    result = retrieve(dataset_id='dataset')
    assert {s['item_id'] for s in result['knowledge']['sources']} == {'approved', 'dependency'}
    assert result['knowledge']['resolution'] == 'resolved'
    assert next(s for s in result['knowledge']['sources'] if s['item_id'] == 'dependency')['binding_version'] == 1
    assert ChatKnowledge.model_validate_json(json.dumps(result['knowledge'])).availability == 'available'
    with knowledge.sessions() as db:
        assert db.get(KnowledgeContext, result['knowledge']['context_id']).user_id == 'test'


def test_missing_dependency_is_not_supplied_or_revealed_in_source_choices(knowledge):
    dep = add(knowledge, 'dependency', name='민감한 원천 설명', mapped=True)
    add(knowledge, 'parent', references=[dep])
    knowledge.physical.clear()
    result = retrieve()
    assert result['knowledge']['sources'] == []
    assert result['knowledge']['blocking'] is True
    assert result['semantic_context']['items'] == []
    assert '민감한' not in json.dumps(result, ensure_ascii=False)


def test_invalid_related_evidence_blocks_generation_and_preview_but_no_match_does_not(knowledge, monkeypatch):
    add(knowledge, 'one', mapped=True)
    knowledge.physical[0]['columns'] = [{'name': 'amount', 'type': 'text'}]
    context = retrieve()
    assert context['knowledge']['blocking'] is True
    assert context['semantic_context']['chat_blocking_gaps'] is True
    assert retrieve(question='무관한 SQL 요청')['knowledge']['blocking'] is False
    class Adapter:
        def __init__(self):
            self.context = context
            self.actor_id = 'test'
        async def run(self, skill, **kwargs):
            assert skill == 'route', 'Invalid business evidence must stop before model generation or SQL'
            return Route(kind='route', intent='query'), None
    async def run():
        return [item async for item in chat_workflow.run_workflow(assistant.ChatRequest(message='검사 기준 조회'), Adapter())]
    output = dict(asyncio.run(run()))
    assert output['clarification']['knowledge']['blocking'] is True and 'proposal' not in output
    monkeypatch.setattr(chat_tools, 'validate_query', lambda *a, **k: pytest.fail('Blocked evidence cannot execute SQL'))
    proposal = ProposalDefinition(kind='query', knowledge_context_id=context['knowledge']['context_id'],
        dataset={'name': 'Query', 'spec': {'source_id': 'measurements', 'raw_sql': 'SELECT amount FROM public.measurements'}})
    with pytest.raises(HTTPException) as caught:
        chat_tools.preview_proposal(proposal, None, actor_id='test')
    assert caught.value.status_code == 409


def test_same_database_with_a_new_mount_revision_invalidates_old_evidence(knowledge, monkeypatch):
    add(knowledge, 'one')
    context = retrieve()['knowledge']
    monkeypatch.setattr(database, 'active_source', database.SourceBinding(None, 'knowledge-db', revision=2, ignored_tables=()))
    for action in (lambda: retrieve(previous_context_id=context['context_id']),
                   lambda: checked_knowledge(context['context_id'], 'test', None)):
        with pytest.raises(HTTPException) as caught:
            action()
        assert caught.value.status_code == 409
    assert restore_knowledge(context, 'test', None)['availability'] == 'unavailable'


def test_ambiguous_meaning_stops_before_sql_or_card_generation(knowledge):
    add(knowledge, 'one', domain='검사')
    add(knowledge, 'two', domain='출하')
    context = retrieve()
    class Adapter:
        def __init__(self):
            self.context = context
            self.actor_id = 'test'
            self.calls = []
        async def run(self, skill, **kwargs):
            self.calls.append(skill)
            assert skill == 'route'
            return Route(kind='route', intent='card'), None
    adapter = Adapter()
    async def run():
        return [item async for item in chat_workflow.run_workflow(assistant.ChatRequest(message='검사 기준 카드'), adapter)]
    output = dict(asyncio.run(run()))
    assert adapter.calls == ['route']
    assert output['clarification']['knowledge']['resolution'] == 'needs_clarification'
    assert {o['item_id'] for o in output['clarification']['knowledge']['clarifications'][0]['options']} == {'one', 'two'}


def test_choice_must_belong_to_presented_context_actor_database_and_project(knowledge, monkeypatch):
    first, second = add(knowledge, 'one'), add(knowledge, 'two')
    add(knowledge, 'unrelated', name='다른 업무')
    context_id = retrieve()['knowledge']['context_id']
    chosen = retrieve(previous_context_id=context_id, selected_definitions=[KnowledgeDefinitionRef(**first)])
    assert [s['item_id'] for s in chosen['knowledge']['sources']] == ['one']
    assert chosen['knowledge']['resolution'] == 'resolved'
    assert chosen['knowledge']['selected_definitions'] == [first]
    for kwargs, status in [({'selected_definitions': [first]}, 422),
                           ({'previous_context_id': context_id, 'selected_definitions': [{'item_id': 'unrelated', 'version': 1}]}, 422),
                           ({'previous_context_id': context_id, 'selected_definitions': [first, second]}, 422),
                           ({'previous_context_id': context_id, 'actor_id': 'other'}, 404),
                           ({'previous_context_id': context_id, 'project_id': 1}, 404)]:
        with pytest.raises(HTTPException) as caught:
            retrieve(**kwargs)
        assert caught.value.status_code == status
    monkeypatch.setattr(database, 'active_source', database.SourceBinding(None, 'other-db', revision=2, ignored_tables=()))
    with pytest.raises(HTTPException) as caught:
        retrieve(previous_context_id=context_id)
    assert caught.value.status_code == 404


@pytest.mark.parametrize('change', ['definition_retired', 'mapping_retired', 'column_changed', 'hidden', 'dependency_retired'])
def test_preview_rejects_changed_evidence_before_query_and_history_redacts(knowledge, monkeypatch, change):
    dependency = add(knowledge, 'dependency', name='원천 의미', mapped=True)
    add(knowledge, 'one', mapped=True, references=[dependency])
    context = retrieve()['knowledge']
    with knowledge.sessions.begin() as db:
        if change == 'definition_retired':
            db.get(KnowledgeVersion, ('one', 1)).status = 'retired'
        elif change == 'mapping_retired':
            db.get(BindingVersion, ('one', 1, 'knowledge-db', 1)).status = 'retired'
        elif change == 'dependency_retired':
            db.get(KnowledgeVersion, ('dependency', 1)).status = 'retired'
    if change == 'column_changed':
        knowledge.physical[0]['columns'] = [{'name': 'amount', 'type': 'text'}]
    if change == 'hidden':
        monkeypatch.setattr(database, 'active_source', database.SourceBinding(None, 'knowledge-db', revision=2,
                            ignored_tables=(('public', 'measurements'),)))
    monkeypatch.setattr(chat_tools, 'validate_query', lambda *a, **k: pytest.fail('SQL must not execute after evidence becomes invalid'))
    proposal = ProposalDefinition(kind='query', knowledge_context_id=context['context_id'],
                                  dataset={'name': 'Query', 'spec': {'source_id': 'measurements', 'raw_sql': 'SELECT amount FROM public.measurements'}})
    with pytest.raises(HTTPException) as caught:
        chat_tools.preview_proposal(proposal, None, actor_id='test')
    assert caught.value.status_code == 409
    restored = restore_knowledge(context, 'test', None)
    assert restored['availability'] == 'unavailable'
    assert restored['sources'] == [] and restored['clarifications'] == []
    from app.chat_history import model_history
    old = SimpleNamespace(status='completed', event='proposal', user_text='질문', response={
        'kind': 'query', 'explanation': '민감한 옛 설명', 'dataset': {'sql': '민감한 SQL'}, 'knowledge': context})
    assert '민감한' not in json.dumps(model_history([old], set(), user_id='test'), ensure_ascii=False)


def test_followup_keeps_exact_approved_version_when_newer_definition_is_published(knowledge):
    add(knowledge, 'one')
    first = retrieve()['knowledge']
    with knowledge.sessions.begin() as db:
        item = db.get(KnowledgeItem, 'one')
        item.head_version = item.published_version = 2
        db.add(KnowledgeVersion(item_id='one', version=2, status='approved', created_by='owner',
                               content=Content(name='검사 기준', description='새 승인 정의').for_kind('term')))
    followup = retrieve(question='동일 기준으로 계속', previous_context_id=first['context_id'])['knowledge']
    assert followup['sources'][0]['version'] == 1
    assert followup['warnings']
    assert checked_knowledge(followup['context_id'], 'test', None)['sources'][0]['version'] == 1


@pytest.mark.parametrize('has_knowledge', [True, False])
def test_real_chat_sse_supplies_approved_context_and_preserves_generic_sql(knowledge, routing, monkeypatch, has_knowledge):
    if has_knowledge:
        add(knowledge, 'one')
    monkeypatch.setattr(assistant, 'proposal_catalog', lambda *a, **k: (
        [{'id': 'dataset', 'version': 1}], [{'id': 'measurements'}]))
    definition = {'name': 'Query', 'spec': {'source_id': 'measurements', 'raw_sql': 'SELECT amount FROM public.measurements'}}
    preview = {'columns': [{'name': 'amount', 'type': 'numeric'}], 'rows': [{'amount': 1}], 'meta': {}, 'next_cursor': None}
    monkeypatch.setattr(chat_tools, 'validate_query', lambda *a, **k: {'definition': definition, 'columns': preview['columns'], 'preview': preview})
    routing.replies.extend([{'kind': 'route', 'intent': 'query'},
                           {'kind': 'query', 'explanation': '검증한 조회', 'name': 'Query', 'source_id': 'measurements', 'sql': definition['spec']['raw_sql']}])
    output = events(routing.client.post('/api/chat', json={'message': '검사 기준으로 조회', 'dataset_id': 'dataset'}))
    assert 'error' not in output, output
    evidence = output['proposal']['knowledge']
    assert evidence['resolution'] == ('resolved' if has_knowledge else 'missing_knowledge')
    assert evidence['blocking'] is False
    assert output['proposal']['knowledge_context_id'] == evidence['context_id']
    supplied = json.loads(routing.observed[1]['messages'][0]['content'].split('Registered context:\n')[1])
    assert len(supplied['semantic_context']['items']) == int(has_knowledge)
    assert output['proposal']['query_result']['rows'] == [{'amount': 1}]


def test_proposal_rejects_client_authored_source_metadata():
    with pytest.raises(ValueError):
        ProposalDefinition.model_validate({'kind': 'query', 'knowledge': {'sources': ['forged']},
            'dataset': {'name': 'Query', 'spec': {'source_id': 'x', 'raw_sql': 'SELECT 1'}}})
