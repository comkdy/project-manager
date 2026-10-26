"""Two independent scheduler connections must dispatch just one durable slot."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete, func, select

from app import db as database
from app.analytics import schedules, worker
from app.analytics.data import REQUIRED
from app.analytics.models import AnalysisNotification, AnalysisRun, AnalysisSchedule
from app.analytics.schemas import AnalysisRequest
from app.analytics.service import create_run, METHOD_VERSION
from app.models import Audit, Source, User, utcnow
from test_project_datasets import api


def test_two_schedulers_dispatch_one_slot(api, monkeypatch):
    key, actor_id, schedule_id = uuid4().hex, uuid4().hex, str(uuid4())
    binding=database.SourceBinding(None,key,ignored_tables=())
    actor=SimpleNamespace(id=actor_id,role='admin',active=True)
    monkeypatch.setattr(worker,'tick',lambda:False)
    monkeypatch.setattr(worker,'wake_worker',lambda:None)
    try:
        with database.use_source(binding),database.SessionLocal.begin() as db:
            db.add(User(id=actor_id,username=actor_id,role='admin'))
            db.add(Source(id=key,connection_key=key,name='scheduled race',schema_name='public',table_name='epm_data',
                primary_key='record_id',columns=[{'name':c,'type':'text'} for c in REQUIRED['epm']]))
            db.flush()
            run=create_run(db,AnalysisRequest(kind='statistics',family='epm',start=utcnow()-timedelta(days=2),end=utcnow()),actor)
            run.status,run.result='completed',{'series':[]}
            db.add(AnalysisSchedule(id=schedule_id,connection_key=key,created_by=actor_id,origin_run_id=run.id,
                name='race',request=run.request,source_snapshot=run.source_snapshot,method_version=METHOD_VERSION,
                interval_hours=1,next_run_at=utcnow()-timedelta(seconds=1)))
        barrier=Barrier(2)
        def tick():
            with database.use_source(binding):
                barrier.wait(timeout=5)
                schedules.scheduler_tick()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(tick),pool.submit(tick)
            a.result(timeout=10);b.result(timeout=10)
        with database.SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.connection_key==key))==2
            schedule=db.get(AnalysisSchedule,schedule_id)
            child=db.get(AnalysisRun,schedule.last_run_id)
            assert child.schedule_id==schedule_id and child.status=='queued'
    finally:
        with database.SessionLocal.begin() as db:
            db.execute(delete(AnalysisNotification).where(AnalysisNotification.schedule_id==schedule_id))
            db.execute(delete(AnalysisSchedule).where(AnalysisSchedule.id==schedule_id))
            db.execute(delete(AnalysisRun).where(AnalysisRun.connection_key==key))
            db.execute(delete(Audit).where(Audit.user_id==actor_id))
            db.execute(delete(Source).where(Source.id==key))
            db.execute(delete(User).where(User.id==actor_id))
