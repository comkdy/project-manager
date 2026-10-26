"""Proposal apply and dependency-lock checks against disposable audit metadata."""
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError

from app.chat_knowledge import checked_knowledge, retrieve_knowledge
from app.db import SessionLocal
from app.models import Audit
from app.semantic.models import KnowledgeContext, KnowledgeItem, KnowledgeVersion
from app.semantic.schemas import Content
from test_chat_workflow import api, counts, SQL


@pytest.fixture
def evidence(api):
    actor_id = api.get('/api/bootstrap').json()['user']['id']
    parent_id, child_id = str(uuid.uuid4()), str(uuid.uuid4())
    name = 'QA 업무지식 ' + parent_id
    with SessionLocal.begin() as db:
        for identity, content in [
            (child_id, Content(name='필수 의존 지식', description='검토된 의존 의미')),
            (parent_id, Content(name=name, description='검토된 업무 의미', references=[{'item_id': child_id, 'version': 1}]))]:
            db.add(KnowledgeItem(id=identity, kind='term', published_version=1))
            db.add(KnowledgeVersion(item_id=identity, version=1, content=content.for_kind('term'),
                                    status='approved', created_by=actor_id))
    context = retrieve_knowledge(name, actor_id, 1)['knowledge']
    yield {'metadata': context, 'actor_id': actor_id, 'parent': parent_id, 'child': child_id}
    with SessionLocal.begin() as db:
        db.execute(delete(KnowledgeContext).where(KnowledgeContext.id == context['context_id']))
        db.execute(delete(KnowledgeVersion).where(KnowledgeVersion.item_id.in_([parent_id, child_id])))
        db.execute(delete(KnowledgeItem).where(KnowledgeItem.id.in_([parent_id, child_id])))


def test_preview_and_apply_reject_retired_dependency_without_saving(api, evidence):
    context = evidence['metadata']
    body = {'project_id': 1, 'workspace_token': api.get('/api/bootstrap').json()['workspace_token'],
            'proposal': {'kind': 'dataset', 'knowledge_context_id': context['context_id'],
                         'dataset': {'name': 'QA knowledge dataset', 'spec': {'source_id': 'inline_measurements', 'raw_sql': SQL}}}}
    preview = api.post('/api/chat/preview', json=body)
    assert preview.status_code == 200, preview.text
    assert {s['item_id'] for s in preview.json()['knowledge']['sources']} == {evidence['parent'], evidence['child']}
    with SessionLocal.begin() as db:
        db.get(KnowledgeVersion, (evidence['child'], 1)).status = 'retired'
    before = counts()
    action_id = str(uuid.uuid4())
    assert api.post('/api/chat/preview', json=body).status_code == 409
    result = api.post('/api/chat/apply', json={**body, 'request_id': action_id})
    assert result.status_code == 409, result.text
    assert counts() == before
    with SessionLocal() as db:
        assert not db.query(Audit).filter(Audit.entity_id == action_id).first()


def test_apply_evidence_lock_also_protects_required_dependencies(evidence):
    def retire():
        with SessionLocal.begin() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '150ms'"))
            writer.execute(text('UPDATE app_semantic_versions SET status=:status WHERE item_id=:id AND version=1'),
                           {'status': 'retired', 'id': evidence['child']})
    with SessionLocal.begin() as db:
        checked_knowledge(evidence['metadata']['context_id'], evidence['actor_id'], 1, db=db, lock=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(DBAPIError) as caught:
                pool.submit(retire).result(timeout=5)
        assert getattr(caught.value.orig, 'sqlstate', None) == '55P03'
    with SessionLocal() as db:
        assert db.get(KnowledgeVersion, (evidence['child'], 1)).status == 'approved'


def test_related_knowledge_gap_cannot_be_applied_as_an_empty_context(api, evidence):
    with SessionLocal.begin() as db:
        child = db.get(KnowledgeVersion, (evidence['child'], 1))
        child.content = {**child.content, 'unresolved': ['의존 정의 재검토 필요']}
        query = db.get(KnowledgeVersion, (evidence['parent'], 1)).content['name']
    context = retrieve_knowledge(query, evidence['actor_id'], 1)['knowledge']
    assert context['blocking'] is True and context['sources'] == []
    body = {'project_id': 1, 'workspace_token': api.get('/api/bootstrap').json()['workspace_token'],
            'proposal': {'kind': 'dataset', 'knowledge_context_id': context['context_id'],
                         'dataset': {'name': 'QA blocked knowledge', 'spec': {'source_id': 'inline_measurements', 'raw_sql': SQL}}}}
    before = counts()
    assert api.post('/api/chat/preview', json=body).status_code == 409
    assert api.post('/api/chat/apply', json={**body, 'request_id': str(uuid.uuid4())}).status_code == 409
    assert counts() == before
    with SessionLocal.begin() as db:
        db.execute(delete(KnowledgeContext).where(KnowledgeContext.id == context['context_id']))
