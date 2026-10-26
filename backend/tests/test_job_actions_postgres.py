"""Cross-connection retry serialization on disposable PostgreSQL metadata."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete, select, func

from app import db as database, job_actions
from app.activity_contract import ActivityActionRequest
from app.analytics import worker
from app.analytics.models import AnalysisRun
from app.analytics.schemas import AnalysisRequest
from app.analytics.service import create_run
from app.analytics.data import REQUIRED
from app.models import Audit, Source, User
from test_project_datasets import api


def test_two_connections_retry_exactly_once_preserving_parent(api, monkeypatch):
    key, actor_id = uuid4().hex, uuid4().hex
    binding = database.SourceBinding(None, key, ignored_tables=())
    actor = SimpleNamespace(id=actor_id, role='admin', active=True)
    monkeypatch.setattr(job_actions, 'wake_worker', lambda: None)
    monkeypatch.setattr(worker, 'tick', lambda: False)
    try:
        with database.use_source(binding), database.SessionLocal.begin() as db:
            db.add(User(id=actor_id, username=actor_id, role='admin'))
            db.add(Source(id=key, connection_key=key, name='race', schema_name='public', table_name='epm_data',
                primary_key='record_id', columns=[{'name': c, 'type': 'text'} for c in REQUIRED['epm']]))
            db.flush()
            parent = create_run(db, AnalysisRequest(kind='statistics',family='epm'), actor)
            parent.status = 'failed'
            identity = parent.id
        body = ActivityActionRequest(target={'kind':'analysis_run','id':identity},action='retry',
            expected_revision=1, workspace_token=key+':0')
        ready = Barrier(2)
        def retry():
            with database.use_source(binding):
                ready.wait(timeout=5)
                return job_actions.analysis_action(body, actor)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(retry), pool.submit(retry)
            a, b = first.result(timeout=10), second.result(timeout=10)
        assert a.target.id == b.target.id and a.target.id != identity
        with database.SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.connection_key==key)) == 2
            parent = db.get(AnalysisRun, identity)
            assert parent.status == 'failed' and parent.retry_run_id == a.target.id
            assert db.get(AnalysisRun, a.target.id).retry_of == identity
    finally:
        with database.SessionLocal.begin() as db:
            db.execute(delete(AnalysisRun).where(AnalysisRun.connection_key==key))
            db.execute(delete(Audit).where(Audit.user_id==actor_id))
            db.execute(delete(Source).where(Source.id==key))
            db.execute(delete(User).where(User.id==actor_id))
