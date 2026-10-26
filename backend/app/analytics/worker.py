import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from threading import Event

from sqlalchemy import or_, select

from .. import db as database
from ..database_settings import binding_for
from ..models import DatabaseMount, DatabaseProfile, utcnow
from .. import worker_observation
from .models import AnalysisRun
from .service import finish_cancel, process_run, serialize_creation

logger = logging.getLogger(__name__)
_wakeup = Event()


def wake_worker():
    """Thread-safe local notification; DB polling still covers other processes."""
    _wakeup.set()


def claim():
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        expired = list(db.scalars(select(AnalysisRun).where(AnalysisRun.status.in_(['running', 'cancelling']),
            or_(AnalysisRun.lease_until < utcnow(), AnalysisRun.lease_until.is_(None))).with_for_update()))
        for run in expired:
            if run.status == 'cancelling':
                finish_cancel(run)
                continue
            run.status, run.error, run.completed_at = "failed", "분석 실행이 중단되었습니다. 같은 조건으로 다시 실행하세요.", utcnow()
            run.lease_until = None
            run.revision += 1
        db.flush()
        if db.scalar(select(AnalysisRun.id).where(AnalysisRun.status.in_(['running', 'cancelling'])).limit(1)):
            return None
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.status == "queued").order_by(AnalysisRun.created_at).limit(1))
        if run is None:
            return None
        run.status, run.started_at, run.lease_until = "running", utcnow(), utcnow() + timedelta(minutes=15)
        run.revision += 1
        return run.id, run.connection_key


def tick():
    job = claim()
    if job is None:
        return False
    identity, key = job
    owned_engine = None
    try:
        with database.SessionLocal() as db:
            profile = db.get(DatabaseProfile, key)
            if profile:
                row = db.get(AnalysisRun, identity)
                if row.kind == "evaluation":
                    mount = db.get(DatabaseMount, 1)
                    if not mount or mount.profile_id != key:
                        raise ValueError("source_unavailable")
                    binding = binding_for(profile, mount.revision)
                else:
                    binding = binding_for(profile)
                owned_engine = binding.engine
            elif database.current_source().key == key:
                binding = database.current_source()
            else:
                raise ValueError("source_unavailable")
        with database.use_source(binding):
            process_run(identity)
    except Exception:
        with database.SessionLocal.begin() as db:
            run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == identity).with_for_update())
            if run and run.status == 'cancelling':
                finish_cancel(run)
            elif run and run.status == "running":
                run.status, run.error, run.completed_at, run.lease_until = "failed", "분석 대상 DB 연결을 확인하세요.", utcnow(), None
                run.revision += 1
    finally:
        if owned_engine:
            owned_engine.dispose()
    return True


async def loop():
    while True:
        # Clear before claiming so a submission racing with the DB check is
        # retained. Drain queued jobs without adding a delay between each one.
        _wakeup.clear()
        try:
            if await asyncio.to_thread(worker_observation.run, 'analysis', tick):
                continue
        except Exception:
            logger.warning("Parameter analytics worker unavailable.")
        await asyncio.to_thread(_wakeup.wait, 2)


@asynccontextmanager
async def analytics_worker():
    worker_observation.start('analysis')
    task = asyncio.create_task(loop(), name="parameter-analytics")
    try:
        yield
    finally:
        worker_observation.stop('analysis')
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
