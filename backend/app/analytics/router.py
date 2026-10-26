from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import defer

from .. import db as database
from ..auth import get_current_user, require_editor
from ..models import Source
from .data import catalog, validate_snapshot
from .models import AnalysisRun
from .schemas import AnalysisRequest
from .service import create_run, find_run, question_context, run_json
from .worker import wake_worker
from .proposal_contract import AnalysisProposalConfirm
from .proposals import confirm_proposal, get_proposal

router = APIRouter(prefix="/api/analytics", tags=["parameter analytics"])


@router.get("/proposals/{identity}")
def read_proposal(identity: str, project_id: int | None = None, user=Depends(require_editor)):
    return get_proposal(identity, user.id, project_id)


@router.post("/proposals/{identity}/confirm", status_code=202)
def confirm_conditions(identity: str, body: AnalysisProposalConfirm, user=Depends(require_editor)):
    return confirm_proposal(identity, body, user.id)


@router.get("/catalog")
def get_catalog(user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        return catalog(db)


@router.get("/runs")
def list_runs(user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        rows = list(db.scalars(select(AnalysisRun).options(defer(AnalysisRun.result, raiseload=True))
                          .where(AnalysisRun.connection_key == database.source_database_key())
                          .order_by(AnalysisRun.created_at.desc()).limit(100)))
        source_ids = {source["source_id"] for run in rows for source in run.source_snapshot.values()}
        sources = {source.id: source for source in db.scalars(select(Source).where(Source.id.in_(source_ids)))}
        questions = question_context(db, rows)
        result = []
        for run in rows:
            if run.kind == "evaluation":
                continue  # Private case analyses are restored through their scoped chat API.
            try:
                validate_snapshot(db, run.source_snapshot, sources=sources, allow_column_additions=run.status == 'completed')
            except HTTPException:
                continue
            result.append(run_json(db, run, details=False, questions_context=questions, user=user))
        return result


@router.get("/runs/{identity}")
def get_run(identity: str, user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        run = find_run(db, identity)
        if run.kind == "evaluation":
            from ..evaluation_analysis import checked_run, view
            return view(db, checked_run(db, identity, user), user)
        return run_json(db, run, user=user)


@router.post("/runs", status_code=202)
def submit_run(body: AnalysisRequest, user=Depends(require_editor)):
    with database.SessionLocal.begin() as db:
        result = run_json(db, create_run(db, body, user), user=user)
    # Notify only after commit: the worker must be able to see the queued job.
    if result["status"] == "queued":
        wake_worker()
    return result
