"""Restart-safe polling worker. Database leases prevent duplicate model calls."""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from sqlalchemy import select

from .. import db as database
from .. import worker_observation
from ..database_settings import binding_for
from ..models import DatabaseProfile, utcnow
from .collector_models import CollectionSettings, KnowledgeAnswer, KnowledgeQuestion
from .collector_service import finish_collection_cancel, future, generate_questions, process_answer

logger = logging.getLogger(__name__)


def tick():
    with database.SessionLocal.begin() as db:
        expired = db.scalars(select(CollectionSettings).where(
            CollectionSettings.last_status.in_(['running', 'cancelling']),
            (CollectionSettings.lease_until.is_(None)) | (CollectionSettings.lease_until < utcnow())).with_for_update())
        for cfg in expired:
            if cfg.last_status == 'cancelling':
                finish_collection_cancel(cfg)
            else:
                cfg.last_status, cfg.last_message = 'error', '실행 확인 시간이 지났습니다. 상태를 확인한 뒤 재시도하세요.'
                cfg.lease_until, cfg.lease_token = None, None
                cfg.execution_revision += 1
    with database.SessionLocal() as db:
        settings = list(db.scalars(select(CollectionSettings).where(CollectionSettings.enabled.is_(True))))
        pending = list(db.execute(select(KnowledgeAnswer.id, KnowledgeQuestion.connection_key)
                                  .join(KnowledgeQuestion).where(KnowledgeAnswer.status.in_(["queued", "processing"]),
                                  (KnowledgeAnswer.lease_until.is_(None)) | (KnowledgeAnswer.lease_until < utcnow()))
                                  .order_by(KnowledgeAnswer.created_at).limit(3)))
    keys = {cfg.connection_key for cfg in settings if not future(cfg.next_run) and not future(cfg.lease_until)}
    keys.update(key for _, key in pending)
    for key in keys:
        engine = None
        try:
            # Resolve the saved target, never whichever DB a browser happens to select.
            with database.SessionLocal() as db:
                profile = db.get(DatabaseProfile, key)
                if profile:
                    binding = binding_for(profile)
                    engine = binding.engine
                elif database.current_source().key == key:
                    binding = database.current_source()
                else:
                    cfg = db.get(CollectionSettings, key)
                    if cfg:
                        cfg.enabled = False
                        cfg.last_status, cfg.last_message = "error", "수집 대상 DB 등록을 확인하세요."
                        db.commit()
                    continue
            with database.use_source(binding):
                for identity, target in pending:
                    if target == key:
                        process_answer(identity)
                if any(c.connection_key == key and not future(c.next_run) for c in settings):
                    generate_questions(scheduled=True)
        except Exception:
            # Do not log exception text: database and provider errors can contain secrets.
            logger.warning("Knowledge collection job failed; retrying on the next worker tick.")
        finally:
            if engine:
                engine.dispose()


async def loop():
    while True:
        try:
            await asyncio.to_thread(worker_observation.run, 'collection', tick)
        except Exception:
            logger.warning("Knowledge collection worker unavailable.")
        await asyncio.sleep(30)


@asynccontextmanager
async def collection_worker():
    worker_observation.start('collection')
    task = asyncio.create_task(loop(), name="knowledge-collection")
    try:
        yield
    finally:
        worker_observation.stop('collection')
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
