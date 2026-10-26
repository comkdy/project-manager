"""Concurrent app schedulers must not duplicate a changed-data analysis."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4
from sqlalchemy import delete, func, select
from app import db as database, knowledge_watch as watches
from app.analytics import worker
from app.analytics.models import AnalysisRun, KnowledgeWatch
from app.evaluation_analysis import METHOD_VERSION
from app.models import Audit, User, utcnow
from test_project_datasets import api


def test_two_watch_schedulers_create_one_job(api,monkeypatch):
    key,actor_id,identity=uuid4().hex,uuid4().hex,str(uuid4())
    binding=database.SourceBinding(None,key,ignored_tables=())
    monkeypatch.setattr(worker,'tick',lambda:False)
    monkeypatch.setattr(watches,'checked_run',lambda db,run_id,user,**kwargs:db.get(AnalysisRun,run_id))
    monkeypatch.setattr(watches,'_compile',lambda *a,**k:(None,'fixture',{},None))
    monkeypatch.setattr(watches,'data_fingerprint',lambda *a:{'fingerprint':'new','row_count':12})
    try:
        with database.use_source(binding),database.SessionLocal.begin() as db:
            db.add(User(id=actor_id,username=actor_id,role='admin'))
            request={'settings':{'method':'group_compare'},'_workspace_token':database.workspace_token(),
                '_evaluation_search':{'query':'fixture','dataset_id':'fixture'},'_owner_role':'admin'}
            run=AnalysisRun(connection_key=key,kind='evaluation',status='completed',request=request,
                source_snapshot={},created_by=actor_id,result={'data_fingerprint':'old','method_version':METHOD_VERSION})
            db.add(run);db.flush()
            db.add(KnowledgeWatch(id=identity,connection_key=key,created_by=actor_id,origin_run_id=run.id,
                last_run_id=run.id,processed_run_id=run.id,fingerprint='old',next_check_at=utcnow()-timedelta(seconds=1)))
        barrier=Barrier(2)
        def tick():
            with database.use_source(binding):
                barrier.wait(timeout=5);watches.tick()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(tick),pool.submit(tick)
            a.result(timeout=15);b.result(timeout=15)
        with database.SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.connection_key==key))==2
            row=db.get(KnowledgeWatch,identity)
            assert row.enabled and db.get(AnalysisRun,row.last_run_id).status=='queued'
    finally:
        with database.SessionLocal.begin() as db:
            db.execute(delete(KnowledgeWatch).where(KnowledgeWatch.id==identity))
            db.execute(delete(AnalysisRun).where(AnalysisRun.connection_key==key))
            db.execute(delete(Audit).where(Audit.user_id==actor_id))
            db.execute(delete(User).where(User.id==actor_id))
