"""Small, explicit analyses of the selected dataset; existing run lifecycle reused.

No inference of physical joins, treatment assignments or causal effects is made.
Only a user-confirmed set of real output columns is passed to the SQL calculator.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlglot import exp

from . import db as database, query
from .analytics.data import validate_snapshot
from .analytics.models import AnalysisRun
from .analytics.proposals import live_editor, lock_active_workspace
from .analytics.service import finish_cancel, project_scope, serialize_creation
from .dashboard_period import apply_period
from .direct_sql import parse_query
from .job_controls import analysis_controls
from .models import Audit, DatabaseMount, Relation, Source, User, utcnow
from .schemas import CardPeriod, Filter, ISOWeek, QueryContext, StrictModel
from .semantic.collector_service import inventory, question_visible
from .semantic.evaluations import dataset_summary, evaluation_origin, retrieve_evaluation_cases
from .semantic.models import KnowledgeItem, KnowledgeVersion
from .semantic.service import catalog_snapshot, definition_issues, digest

METHOD_VERSION = "evaluation-dataset-v1"
MAX_ROWS = 200_000
MAX_GROUPS = 30
WARNINGS = [
    "선택한 데이터셋의 행을 집계합니다. 행 수는 독립 실험 수가 아니며 재측정·중복 행도 저장된 데이터셋 정의대로 포함됩니다.",
    "그룹 차이와 상관관계는 관찰 결과입니다. 장비·제품·기간 등 다른 조건의 영향을 분리하거나 인과관계를 확정하지 않습니다.",
    "서로 다른 테이블의 측정값을 자동 연결하지 않습니다. 상관분석은 같은 데이터셋 행에 함께 있는 두 수치 컬럼만 사용합니다.",
]


class CaseRef(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)


class SelectedCard(StrictModel):
    dataset_id: str = Field(min_length=1, max_length=80)
    dataset_version: int = Field(ge=1)
    title: str = Field(default="", max_length=200)
    x: str | None = None
    y: str | None = None
    color: str | None = None
    trellis: str | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    period: CardPeriod = Field(default_factory=CardPeriod)


class Search(StrictModel):
    query: str = Field(min_length=1, max_length=12000)
    project_id: int | None = None
    dataset_id: str | None = Field(default=None, max_length=80)
    card: SelectedCard | None = None
    period_start: ISOWeek | None = None


class Prepare(StrictModel):
    case_ref: CaseRef
    suggestion_id: str = Field(min_length=1, max_length=180)
    search: Search
    conversation_id: str | None = Field(default=None, max_length=80)
    turn_sequence: int | None = Field(default=None, ge=1)
    explicit_case: bool = False
    dataset_recommendation_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def paired_origin(self):
        if (self.conversation_id is None) != (self.turn_sequence is None):
            raise ValueError("대화와 메시지 위치를 함께 전달하세요.")
        return self


class Settings(StrictModel):
    method: Literal["group_compare", "correlation", "ml_regression"]
    group_column: str | None = Field(default=None, max_length=160)
    metrics: list[str] = Field(min_length=1, max_length=2)
    baseline: str | None = Field(default=None, max_length=500)
    comparison: str | None = Field(default=None, max_length=500)
    features: list[str] = Field(default_factory=list, max_length=8)
    unit_columns: list[str] = Field(default_factory=list, max_length=4)
    time_column: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def supported(self):
        if self.method == "ml_regression":
            if len(self.metrics) != 1 or not self.features or not self.unit_columns or not self.time_column:
                raise ValueError("예측할 수치 1개, 설명할 항목, Lot·wafer 구분과 측정 시점을 선택하세요.")
            names = [*self.metrics, *self.features, *self.unit_columns, self.time_column]
            if len(set(names)) != len(names) or any(not n or len(n) > 160 for n in names):
                raise ValueError("목표·설명 항목·표본 구분·시점은 서로 다른 실제 컬럼이어야 합니다.")
            if self.group_column or self.baseline is not None or self.comparison is not None:
                raise ValueError("ML 분석에는 그룹 비교 조건을 함께 넣지 않습니다.")
            return self
        if self.features or self.unit_columns or self.time_column:
            raise ValueError("ML 조건은 ML 분석에서만 사용합니다.")
        if len(set(self.metrics)) != len(self.metrics) or any(not v or len(v) > 160 for v in self.metrics):
            raise ValueError("서로 다른 수치 컬럼을 선택하세요.")
        if self.method == "correlation":
            if len(self.metrics) != 2 or self.group_column or self.baseline is not None or self.comparison is not None:
                raise ValueError("상관분석은 두 수치 컬럼만 선택하세요.")
        else:
            if not self.group_column or self.group_column in self.metrics:
                raise ValueError("비교할 구분 컬럼과 수치 컬럼을 각각 선택하세요.")
            if (self.baseline is None) != (self.comparison is None):
                raise ValueError("차이를 계산할 기준 그룹과 비교 그룹을 함께 입력하세요.")
            if self.baseline is not None and (not self.baseline or not self.comparison or self.baseline == self.comparison):
                raise ValueError("서로 다른 기준 그룹과 비교 그룹을 입력하세요.")
        return self


class Execute(StrictModel):
    expected_revision: int = Field(ge=1)
    workspace_token: str = Field(min_length=1, max_length=200)
    settings: Settings
    continuous: bool = False
    interval_minutes: int = Field(default=60, ge=15, le=10080)


def numeric_type(value):
    value = value.lower().split("(", 1)[0].strip()
    return value in {"smallint", "integer", "bigint", "real", "double precision", "numeric", "decimal", "int2", "int4", "int8", "float4", "float8"}


def _compile(search, *, check_types=False):
    """Use the same authored query, ownership, filters and period as the card."""
    identity = search.dataset_id or (search.card.dataset_id if search.card else None)
    if not identity:
        raise HTTPException(422, "현재 데이터로 비교하려면 프로젝트의 데이터셋이나 카드를 먼저 선택하세요.")
    if search.card and search.card.dataset_id != identity:
        raise HTTPException(422, "선택한 카드와 데이터셋이 다릅니다. 현재 선택을 다시 확인하세요.")
    dataset, catalog = query.load_dataset(identity, with_catalog=True)
    if search.card and search.card.dataset_version != dataset.version:
        raise HTTPException(409, "선택한 카드의 데이터셋 버전이 변경되었습니다. 새로 조회한 뒤 분석하세요.")
    context = query.scoped_context(dataset.project_id, QueryContext(project_id=search.project_id, period_start=search.period_start))
    compiled = query.validate_dataset(dataset.spec, catalog=catalog)
    if "direct_sql" in compiled and any(isinstance(node, (exp.Rand, exp.TableSample))
                                       for node in parse_query(compiled["direct_sql"]).walk()):
        raise HTTPException(422, "임의 추출이나 random()을 사용하는 데이터셋은 반복 조회마다 대상이 달라집니다. 사례 분석에는 고정된 조건의 데이터셋을 선택하세요.")
    compiled["_evaluation_definition_digest"] = digest({"spec": dataset.spec, "columns": dataset.columns, "version": dataset.version})
    compiled["columns"] = dataset.columns
    if check_types:
        actual = query._schema_types(compiled)
        if {c["name"] for c in actual} != {c["name"] for c in dataset.columns}:
            raise HTTPException(409, "데이터셋의 실제 출력 컬럼이 변경되었습니다. 데이터셋을 확인하고 저장하세요.")
        compiled["columns"] = actual
    period_card = SimpleNamespace(period=search.card.period if search.card else CardPeriod())
    compiled, period = apply_period(compiled, period_card, context)
    filters = search.card.filters if search.card else []
    cte, params, ignored = query._dataset_cte(compiled, context, filters, include_row_id=False)
    if ignored:
        raise HTTPException(422, "현재 조회 조건을 모두 적용할 수 없습니다: " + ", ".join(ignored))
    return compiled, cte, params, period


def _case(db, reference, dataset, project_id):
    from .semantic.evaluation_context import dataset_changed
    from .semantic.evaluation_results import computed_content_issues
    item = db.get(KnowledgeItem, reference["id"])
    version = db.get(KnowledgeVersion, (reference["id"], reference["version"]))
    if (not item or item.kind != "evaluation" or item.connection_key != database.source_database_key()
            or item.published_version != reference["version"] or not version or version.status != "approved"
            or definition_issues(db, item, version, catalog_snapshot(db))
            or computed_content_issues(db, item.id, version.content)):
        raise HTTPException(409, "참고 사례의 승인 상태가 변경되었습니다. 관련 사례를 다시 확인하세요.")
    origin = evaluation_origin(db, item.id, version.version)
    datasets = inventory(db)
    if (origin is None or origin.dataset.get("project_id") not in {None, project_id}
            or not question_visible(origin, datasets, db=db) or dataset_changed(origin, datasets)):
        raise HTTPException(409, "참고 사례의 데이터 범위가 변경되었습니다. 사례를 다시 검토하세요.")
    refs = [ref for term in version.content.get('details', {}).get('terms', []) for ref in term.get('references', [])]
    if refs:
        from .semantic.evaluation_terms import approved_definitions, reference_matches
        definitions = approved_definitions(db)
        if any(not reference_matches(ref, definitions.get(ref['item_id'])) for ref in refs):
            raise HTTPException(409, '참고 사례의 용어·데이터 연결이 변경되었습니다. 확인 요청에서 연결을 다시 검토하세요.')
    return version


def check_scope(db, run):
    """Also used by generic run reads/retries so no alternate route skips checks."""
    # Completed evidence survives switching away from and back to the same DB.
    # Work still waiting/running/retrying must retain the exact confirmed epoch.
    if run.status != "completed" and run.request.get("_workspace_token") != database.workspace_token():
        raise HTTPException(409, "DB 설정이 변경되었습니다. 현재 데이터에서 분석을 다시 준비하세요.")
    binding = database.current_source()
    if binding.profile_id:
        mount = db.get(DatabaseMount, 1)
        if not mount or mount.profile_id != binding.profile_id or mount.revision != binding.revision:
            raise HTTPException(409, "활성 DB가 변경되었습니다. 현재 DB에서 분석을 다시 준비하세요.")
    from .source_compatibility import dataset_compatible, sources_compatible
    historical = run.status == 'completed'
    validate_snapshot(db, run.source_snapshot, allow_column_additions=historical)
    stored = run.request["_evaluation_dataset"]
    current = inventory(db).get(stored["id"])
    if current is None or not (dataset_compatible(stored, current) if historical else digest(current) == digest(stored)):
        raise HTTPException(409, "데이터셋 또는 원천 정의가 변경되었습니다. 현재 데이터로 새 분석을 준비하세요.")
    binding, previous = execution_binding(db, current), run.request.get('_evaluation_binding') or {}
    binding_matches = (binding.get('relations') == previous.get('relations') and
        sources_compatible(previous.get('sources', []), binding.get('sources', []))) if historical else binding == previous
    if not binding_matches:
        raise HTTPException(409, "데이터셋의 연결 조건이 변경되었습니다. 새 분석을 준비하세요.")
    if run.request.get("_project_scope") != project_scope(db, run.request.get("project_id")):
        raise HTTPException(409, "프로젝트 범위가 변경되었습니다. 새 분석을 준비하세요.")
    if run.request.get('_chat_analysis'):
        if run.request['_chat_analysis'].get('version') != 1:
            raise HTTPException(409, '분석 요청의 출처를 확인할 수 없습니다.')
        from .chat_data_analysis import facts_for_run
        facts_for_run(db, run)
    else:
        _case(db, run.request["_evaluation_case"], stored, run.request.get("project_id"))
    from .evaluation_recommendation import check_basis
    check_basis(db, run)


def execution_binding(db, dataset):
    sources = [query.source_json(db.get(Source, s["id"])) for s in dataset["sources"]]
    relations = [db.get(Relation, identity) for identity in dataset["spec"].get("joins", [])]
    return {"sources": sources, "relations": [{c.name: getattr(r, c.name) for c in Relation.__table__.columns} for r in relations]}


def checked_run(db, identity, user, *, lock=False):
    statement = select(AnalysisRun).where(AnalysisRun.id == identity, AnalysisRun.kind == "evaluation",
                                         AnalysisRun.connection_key == database.source_database_key())
    run = db.scalar(statement.with_for_update() if lock else statement)
    if run is None:
        raise HTTPException(404, "현재 DB에서 이 사례 분석을 찾을 수 없습니다.")
    actor = live_editor(db, user.id)
    if actor.id != run.created_by and actor.role != "admin":
        raise HTTPException(403, "본인이 요청한 사례 분석만 확인할 수 있습니다.")
    owner = db.get(User, run.created_by)
    if not owner or not owner.active or owner.role != run.request.get("_owner_role"):
        raise HTTPException(409, "요청자의 권한이 변경되었습니다. 현재 권한으로 분석을 다시 준비하세요.")
    check_scope(db, run)
    return run


def _quota(db, actor, *, preparing=False):
    active = db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running", "cancelling"])))
    if not preparing and active >= 5:
        raise HTTPException(429, "대기 중인 분석이 5개입니다. 완료 후 실행하세요.")
    if preparing:
        recent = db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.created_by == actor.id,
                          AnalysisRun.created_at > utcnow() - timedelta(hours=1)))
        if recent >= 30:
            raise HTTPException(429, "분석 준비는 사용자별 시간당 30회까지 가능합니다.")


def view(db, run, user):
    from .semantic.evaluation_results import review_state
    from .evaluation_recommendation import public_recommendation
    from .measurement_items import display_result
    request = run.request
    case = request.get("_evaluation_case") or {}
    return dict(id=run.id, kind="evaluation", revision=run.revision, status=run.status,
        workspace_token=database.workspace_token() if run.status == "completed" else request["_workspace_token"], project_id=request.get("project_id"),
        dataset=dataset_summary(request["_evaluation_dataset"]), case_ref={k: case[k] for k in ("id", "version", "name")} if case else None,
        origin='chat' if request.get('_chat_analysis') else 'case',
        question=request.get('_chat_analysis', {}).get('question'),
        knowledge_basis={key: case.get('details', {}).get(key, '') for key in
            ('operation', 'recipe_changes', 'inline_observations', 'epm_observations', 'conditions', 'limitations')},
        columns=request["_evaluation_columns"], scope=request["_evaluation_scope"], settings=request.get("settings"),
        recommendation=public_recommendation(db, run),
        result=display_result(run.result, request), review=review_state(db, run), controls=analysis_controls(run, user).model_dump(mode="json"),
        error=run.error, warnings=WARNINGS, created_at=run.created_at, started_at=run.started_at, completed_at=run.completed_at,
        retry_of=run.retry_of, retry_run_id=run.retry_run_id, source_watermark=run.source_watermark,
        limits={"max_rows": MAX_ROWS, "max_groups": MAX_GROUPS})


def prepare(body, user):
    from .evaluation_chat import suggestions_for
    search = body.search
    # Resolve ownership before retrieval and persist the resolved scope, including
    # when a caller selected a project dataset without explicitly naming it.
    compiled, _, _, period = _compile(search, check_types=True)
    identity = search.dataset_id or search.card.dataset_id
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        actor = live_editor(db, user.id)
        datasets = inventory(db)
        selected = datasets.get(identity)
        if selected is None:
            raise HTTPException(404, "현재 데이터셋을 찾을 수 없습니다.")
        if compiled["_evaluation_definition_digest"] != digest({k: selected[k] for k in ("spec", "columns", "version")}):
            raise HTTPException(409, "준비 중 데이터셋이 변경되었습니다. 현재 데이터셋으로 다시 준비하세요.")
        project_id = selected.get("project_id") if selected.get("project_id") is not None else search.project_id
        discovery = None
        if body.dataset_recommendation_id:
            from .evaluation_datasets import analysis_link
            discovery = analysis_link(db, body.dataset_recommendation_id, actor, body.case_ref.model_dump(), identity, project_id)
            if discovery.run_id:
                return view(db, checked_run(db, discovery.run_id, actor), actor)
        search = search.model_copy(update={"dataset_id": identity, "project_id": project_id})
        if body.explicit_case:
            version = _case(db, body.case_ref.model_dump(), selected, project_id)
            case = {'id': body.case_ref.id, 'version': version.version, **deepcopy(version.content)}
        else:
            found = retrieve_evaluation_cases(db, search.query, project_id=project_id, dataset_id=identity,
                                             card=search.card, limit=8)
            case = next((c for c in found["cases"] if (c["id"], c["version"]) == (body.case_ref.id, body.case_ref.version)), None)
        if case is None or body.suggestion_id not in {s["id"] for s in suggestions_for(case)}:
            raise HTTPException(409, "현재 범위에서 유효한 승인 사례와 제안을 다시 선택하세요.")
        scope = project_scope(db, project_id)
        _quota(db, actor, preparing=True)
        sources = [db.get(Source, s["id"]) for s in selected["sources"]]
        snapshot = {s.id: {"source_id": s.id, "name": s.name, "schema_name": s.schema_name,
                          "table_name": s.table_name, "columns": s.columns} for s in sources}
        columns = [{**c, "numeric": numeric_type(c["type"])} for c in compiled["columns"]]
        if not any(c["numeric"] for c in columns):
            raise HTTPException(422, "선택한 데이터셋에 계산할 수치 컬럼이 없습니다. 데이터셋 구성을 확인하세요.")
        request = {"kind": "evaluation", "project_id": project_id, "settings": None,
            "_workspace_token": database.workspace_token(), "_owner_role": actor.role, "_project_scope": scope,
            "_evaluation_dataset": selected, "_evaluation_case": case, "_evaluation_search": search.model_dump(mode="json"),
            "_evaluation_binding": execution_binding(db, selected),
            "_evaluation_columns": columns, "_evaluation_suggestion": body.suggestion_id,
            "_evaluation_scope": {"filters": [v.model_dump(mode="json") for v in search.card.filters] if search.card else [],
                "period_start": search.period_start.model_dump(mode="json") if search.period_start else None,
                "period": period, "card_title": search.card.title if search.card else None}}
        run = AnalysisRun(connection_key=database.source_database_key(), kind="evaluation", status="needs_input",
                          request=request, source_snapshot=snapshot, created_by=actor.id, question_ids=[])
        db.add(run)
        db.flush()
        if discovery:
            discovery.run_id, discovery.revision = run.id, discovery.revision + 1
            run.request = {**run.request, '_evaluation_dataset_recommendation_id': discovery.id}
        if body.conversation_id:
            from .chat_history import attach_evaluation_analysis
            attach_evaluation_analysis(db, body.conversation_id, body.turn_sequence, actor.id, project_id,
                                       body.case_ref.model_dump(), body.suggestion_id, run.id)
        db.add(Audit(user_id=actor.id, action="evaluation_analysis.prepare", entity_id=run.id,
                     details={"dataset_id": identity, "case_id": case["id"]}))
        return view(db, run, actor)


def validate_settings(settings, columns):
    names = {c["name"]: c for c in columns}
    if any(c not in names or not numeric_type(names[c]["type"]) for c in settings.metrics):
        raise HTTPException(422, "현재 데이터셋의 실제 수치 컬럼을 선택하세요.")
    if settings.group_column and settings.group_column not in names:
        raise HTTPException(422, "현재 데이터셋의 실제 구분 컬럼을 선택하세요.")
    if settings.method == "ml_regression":
        if any(c not in names for c in [*settings.features, *settings.unit_columns, settings.time_column]):
            raise HTTPException(422, "ML 분석에 사용할 실제 출력 컬럼을 선택하세요.")
        if not names[settings.time_column]['type'].lower().startswith(('timestamp', 'date')):
            raise HTTPException(422, "검증 기간을 나눌 날짜·시간 컬럼을 선택하세요.")


def execute(identity, body, user):
    from .analytics.worker import wake_worker
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        run = checked_run(db, identity, user, lock=True)
        if body.workspace_token != run.request["_workspace_token"]:
            raise HTTPException(409, "작업공간이 변경되었습니다. 현재 조건을 다시 확인하세요.")
        if run.status != "needs_input":
            if body.settings.model_dump(mode="json") == run.request.get("settings") and body.expected_revision <= run.revision:
                return view(db, run, user)
            raise HTTPException(409, "이미 실행한 분석입니다. 현재 상태를 확인하세요.")
        if body.expected_revision != run.revision:
            raise HTTPException(409, "분석 조건이 변경되었습니다. 최신 상태를 확인하세요.")
        validate_settings(body.settings, run.request["_evaluation_columns"])
        from .measurement_items import validate_items
        validate_items(run.request['_evaluation_dataset'], body.settings, run.request['_evaluation_scope']['filters'])
        recommendation = run.request.get('_evaluation_recommendation')
        if recommendation and (recommendation.get('status') != 'ready' or
                body.settings.model_dump(mode='json') != recommendation.get('settings')):
            raise HTTPException(409, '데이터 연결의 확인·승인과 AI 추천을 마친 뒤 표시된 조건으로 실행하세요.')
        if body.continuous and not recommendation:
            raise HTTPException(422, 'AI 추천 조건을 확인한 뒤 지속 분석을 함께 시작할 수 있습니다.')
        _quota(db, user)
        if body.continuous:
            from .evaluation_recommendation import confirm_watch
            confirm_watch(db, run, body.interval_minutes)
        run.request = {**run.request, "settings": body.settings.model_dump(mode="json")}
        run.status, run.revision = "queued", run.revision + 1
        db.add(Audit(user_id=user.id, action="evaluation_analysis.execute", entity_id=run.id,
                     details={"method": body.settings.method}))
        result = view(db, run, user)
    wake_worker()
    return result


def retry_run(db, run, user):
    checked_run(db, run.id, user)
    _quota(db, user, preparing=True)
    _quota(db, user)
    request = deepcopy(run.request)
    request.pop("_evaluation_review", None)
    request["_owner_role"] = user.role
    child = AnalysisRun(connection_key=run.connection_key, kind="evaluation", status="queued", request=request,
                        source_snapshot=deepcopy(run.source_snapshot), created_by=user.id, question_ids=[])
    db.add(child)
    db.flush()
    return child


def process_run(identity):
    from .evaluation_compute import compute
    with database.SessionLocal() as db:
        run = db.get(AnalysisRun, identity)
        if run.status == "cancelling":
            finish_cancel(run)
            db.commit()
            return
        if run.status != "running":
            return
        user = db.get(User, run.created_by)
        if user is None:
            raise HTTPException(403, "요청자의 분석 권한을 확인할 수 없습니다.")
        run = checked_run(db, identity, user)
        stored = deepcopy(run.request)
    def checkpoint():
        with database.SessionLocal() as db:
            current = db.get(AnalysisRun, identity)
            if current.status != "running":
                raise HTTPException(409, "실행이 취소되었습니다.")
    search = Search.model_validate(stored["_evaluation_search"])
    compiled, cte, params, _ = _compile(search, check_types=True)
    settings = Settings.model_validate(stored["settings"])
    validate_settings(settings, compiled["columns"])
    checkpoint()
    result = compute(cte, params, settings, compiled["columns"], stored, checkpoint)
    checkpoint()
    with database.SessionLocal.begin() as db:
        raw = db.scalar(select(AnalysisRun).where(AnalysisRun.id == identity).with_for_update())
        if raw.status == "cancelling":
            finish_cancel(raw)
            return
        if raw.status != "running":
            return
        run = checked_run(db, identity, db.get(User, raw.created_by))
        run.result = result
        run.source_watermark = {"captured_at": result["captured_at"], "row_count": result["row_count"],
            "dataset_id": stored["_evaluation_dataset"]["id"], "dataset_version": stored["_evaluation_dataset"]["version"],
            "scope_digest": digest({"dataset": stored["_evaluation_dataset"], "search": stored["_evaluation_search"]})}
        run.status, run.completed_at, run.lease_until = "completed", utcnow(), None
        run.revision += 1
