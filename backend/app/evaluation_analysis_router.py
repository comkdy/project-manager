from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select

from . import db as database
from .auth import require_editor
from .analytics.models import AnalysisRun
from .evaluation_analysis import Execute, Prepare, checked_run, execute, prepare, view
from .semantic.evaluation_results import SubmitEvaluationReview, submit_review
from .evaluation_recommendation import Recommend, claim, process

router = APIRouter(prefix="/api/evaluation-analysis", tags=["evaluation analysis"])


@router.post("/prepare", status_code=201)
def prepare_analysis(body: Prepare, user=Depends(require_editor)):
    return prepare(body, user)


@router.get("")
def list_analyses(project_id: int | None = None, setup_only: bool = False, user=Depends(require_editor)):
    with database.SessionLocal() as db:
        statement = select(AnalysisRun).where(AnalysisRun.kind == "evaluation",
            AnalysisRun.connection_key == database.source_database_key(), AnalysisRun.created_by == user.id)
        if setup_only:
            statement = statement.where(AnalysisRun.status == 'needs_input')
        rows = db.scalars(statement.order_by(AnalysisRun.created_at.desc()).limit(50))
        result = []
        for run in rows:
            if setup_only and (run.status != 'needs_input' or not run.request.get('_evaluation_recommendation')):
                continue
            if not setup_only and run.request.get("project_id") != project_id:
                continue
            try:
                checked_run(db, run.id, user)
            except HTTPException:
                continue
            result.append(view(db, run, user))
        return result


@router.get("/{identity}")
def read_analysis(identity: str, project_id: int | None = None, user=Depends(require_editor)):
    with database.SessionLocal() as db:
        run = checked_run(db, identity, user)
        if run.request.get("project_id") != project_id:
            raise HTTPException(409, "분석을 준비한 프로젝트에서 결과를 확인하세요.")
        return view(db, run, user)


@router.post("/{identity}/execute", status_code=202)
def execute_analysis(identity: str, body: Execute, user=Depends(require_editor)):
    return execute(identity, body, user)


@router.post("/{identity}/recommend", status_code=202)
def recommend_analysis(identity: str, body: Recommend, background: BackgroundTasks, user=Depends(require_editor)):
    result, token = claim(identity, body, user)
    if token:
        background.add_task(process, identity, token, database.current_source())
    return result


@router.post("/{identity}/submit-review", status_code=201)
def submit_analysis_review(identity: str, body: SubmitEvaluationReview, user=Depends(require_editor)):
    return submit_review(identity, body, user)
