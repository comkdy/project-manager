"""Removed backup endpoints leave existing jobs and metadata untouched."""
import json
import shutil
from datetime import timedelta
import uuid

import pytest
from sqlalchemy.engine import make_url
from app import recovery, db as database
from app.models import utcnow
from app.analytics.models import AnalysisRun, AnalysisSchedule
from app.semantic.collector_models import CollectionSettings, KnowledgeAnswer, KnowledgeQuestion
from test_project_datasets import api  # noqa: F401

@pytest.fixture
def backups(api,monkeypatch,tmp_path):
    # Removed endpoints need no backup runtime. Read only isolated test metadata.
    url = make_url(database.settings.app_database_url)
    if (url.host != '127.0.0.1' or url.port != 55432 or not url.database
            or not url.database.startswith(('audit_midterm_', 'qa_bundle', 'wafer_qa_app_'))):
        pytest.skip('Requires isolated test metadata, never the user app DB')
    root=tmp_path/'removed-backup-output'
    monkeypatch.setattr(recovery,'BACKUP_ROOT',root)
    yield url, None

def test_removed_backup_cannot_change_existing_jobs_or_create_files(api,backups,monkeypatch):
    url,runtime=backups
    prefix='restore-'+uuid.uuid4().hex[:10]
    user=api.get('/api/auth/session').json()['user']['id']
    future=utcnow()+timedelta(days=5)
    with database.SessionLocal.begin() as db:
        db.add(AnalysisRun(id=prefix,connection_key='test-only',kind='trend',status='running',request={'knowledge_refs':None},source_snapshot={},question_ids=[prefix],created_by=user,lease_until=future))
        db.add(KnowledgeQuestion(id=prefix,connection_key='test-only',fingerprint=prefix,title='Recovery fixture',kind='metric',topic='fixture',dataset={}))
        db.flush()
        db.add(KnowledgeAnswer(id=prefix,question_id=prefix,user_id=user,text='test',status='processing',lease_until=future))
        db.add(CollectionSettings(connection_key=prefix,enabled=True,next_run=future,lease_until=future,last_status='running'))
        db.add(AnalysisSchedule(id=prefix,connection_key='test-only',created_by=user,origin_run_id=prefix,name='Recovery fixture',request={},source_snapshot={},method_version='test',interval_hours=24,enabled=True,next_run_at=future))
    with recovery.connect(url) as source:
        before=recovery.inventory(source)
    for path in ('/api/admin/recovery','/api/admin/recovery/old/verify'):
        assert api.get(path).status_code==404
        assert api.post(path,json={'request_id':uuid.uuid4().hex}).status_code in (404,405)
    assert not recovery.BACKUP_ROOT.exists()
    with recovery.connect(url) as source:
        assert recovery.inventory(source)==before
    with database.SessionLocal() as db:
        assert db.get(AnalysisRun,prefix).status=='running'
        assert db.get(KnowledgeAnswer,prefix).status=='processing'
        assert db.get(CollectionSettings,prefix).enabled
        assert db.get(AnalysisSchedule,prefix).enabled
