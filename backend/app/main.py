import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import auth, db as database
from .auth import UserCreate, UserUpdate, get_current_user, lock_user_management, require_admin, require_editor, user_json
from .bootstrap import initial_dashboard, initialize
from .catalog import discover_tables, projects_list, register_source, relation_json, validate_relation
from .db import SessionLocal, settings, source_database_key
from .models import Audit, Dashboard, DatasetVersion, LoginSession, Relation, Source, User
from .query import assistant_catalog, chart_query, chart_response, clear_cache, dataset_json, preview_dataset, source_connection, source_json, table_query, validate_card, validate_dataset
from .query import normalize_definition
from .group_completion import complete_groups
from .simple_editor import prepare_simple_spec, simple_editor_state
from .schemas import ChartRequest, DashboardUpdate, DatasetCreate, DatasetPreviewRequest, DatasetSpec, DatasetUpdate, GroupCompletionRequest, PivotValuesRequest, QueryContext, QueryRequest, RelationCreate, SimpleEditorRequest, SourceCreate
from .table_visibility import TableVisibility, hidden_card_ids, latest_dataset_versions, require_source_visible, source_visible
from .source_discovery import refresh_sources
from .database_settings import router as database_router, load_active_source
from .semantic.router import router as semantic_router
from .semantic.collector_router import router as collection_router
from .semantic.collector_worker import collection_worker
from .llm_settings import router as llm_router, credential_status
from .project_settings import router as project_router
from .analytics.router import router as analytics_router
from .evaluation_analysis_router import router as evaluation_analysis_router
from .evaluation_datasets import router as evaluation_datasets_router
from .analytics.models import migrate_analytics
from .analytics.worker import analytics_worker
from .chat_history import migrate_chat_history, router as chat_history_router
from .dataset_versioning import lock_dataset_version
from .dataset_impact import dataset_impact
from .dataset_impact_contract import DatasetImpact
from .activity import router as activity_router
from .analytics.schedules import router as schedules_router, schedule_worker
from .analytics.history import router as analysis_history_router
from .knowledge_watch import router as knowledge_watch_router
from .chat_data_analysis import router as chat_data_analysis_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    initialize()
    migrate_analytics(database.engine)
    migrate_chat_history(database.engine)
    load_active_source()
    async with collection_worker(), analytics_worker(), schedule_worker():
        yield


app = FastAPI(title="Project Manager", version="0.2.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(database_router)
app.include_router(semantic_router)
app.include_router(collection_router)
app.include_router(llm_router)
app.include_router(project_router)
app.include_router(analytics_router)
app.include_router(evaluation_analysis_router)
app.include_router(evaluation_datasets_router)
app.include_router(chat_history_router)
app.include_router(activity_router)
app.include_router(schedules_router)
app.include_router(analysis_history_router)
app.include_router(knowledge_watch_router)
app.include_router(chat_data_analysis_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = {x.strip().rstrip("/") for x in settings.allowed_origins.split(",") if x.strip()}
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin.rstrip("/") not in allowed):
            return JSONResponse({"detail": "허용되지 않은 요청 Origin입니다."},status_code=403)
        if settings.auth_mode == "sso" and not origin and request.url.path not in {"/api/auth/login", "/api/auth/logout"}:
            return JSONResponse({"detail": "요청 출처를 확인할 수 없습니다."},status_code=403)
    if request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/auth/", "/api/health", "/api/llm/")):
        try:
            binding = await run_in_threadpool(load_active_source)
        except SQLAlchemyError:
            return JSONResponse({"detail": "앱 데이터베이스 연결에 실패했습니다."}, status_code=503)
        with database.use_source(binding):
            expected = request.headers.get("x-workspace-token")
            exempt = request.url.path.startswith(("/api/admin/databases", "/api/database-status", "/api/bootstrap"))
            if expected and not exempt and expected != database.workspace_token():
                return JSONResponse({"detail": "DB 설정이 변경되었습니다. 화면을 새로고침해 주세요."}, status_code=409)
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    messages = [f"{'.'.join(str(v) for v in item['loc'][1:])}: {item['msg']}" for item in error.errors()]
    return JSONResponse({"detail": "; ".join(messages)},status_code=422)


@app.exception_handler(SQLAlchemyError)
async def database_error(request, error):
    # Avoid logging SQL parameters or connection URLs (which may contain credentials).
    logger.error("Database operation failed: %s", type(error).__name__)
    return JSONResponse({"detail": "앱 데이터베이스 연결 또는 저장에 실패했습니다."},status_code=503)


def audit(db,user,action,entity_id,details=None):
    db.add(Audit(user_id=user.id,action=action,entity_id=entity_id,details=details or {}))


def dashboard_json(item, db):
    result = {key:getattr(item,key) for key in ("id","project_id","version","cards","layout")}
    hidden = hidden_card_ids(db, item.cards) | {c["id"] for c in item.cards if c.get("kind") == "heatmap"}
    latest = latest_dataset_versions(db, item.cards)
    result["cards"] = [{**c, "renderer": "plotly", "dataset_version": latest.get(c["dataset_id"], c["dataset_version"])}
                       for c in item.cards if c["id"] not in hidden]
    result["layout"] = [p for p in item.layout if p["i"] not in hidden]
    return result


def require_project(project_id):
    if project_id is not None and project_id not in {p["id"] for p in projects_list()}:
        raise HTTPException(404,"프로젝트를 찾을 수 없습니다.")


@app.get("/api/health")
def health():
    statuses = {}
    for label,engine in (("metadata",database.engine),("source",database.current_source().engine)):
        try:
            if engine is None:
                statuses[label] = "not_configured"
            else:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                statuses[label] = "ok"
        except SQLAlchemyError:
            statuses[label] = "unavailable"
    return {"status":"ok" if all(v=="ok" for v in statuses.values()) else "degraded","dependencies":statuses}


@app.get("/api/bootstrap")
def bootstrap(project_id: int | None = None,user=Depends(get_current_user)):
    require_project(project_id)
    with SessionLocal() as db:
        catalog = refresh_sources(db, project_id)
        db.commit()
    return {"user":user_json(user),"projects":projects_list(),"datasets":assistant_catalog(project_id=project_id),"catalog":catalog,
            "llm_configured":credential_status(user.id)["configured"], "workspace_token": database.workspace_token()}


@app.get("/api/dashboards")
def get_dashboard(project_id: int | None = None,user=Depends(get_current_user)):
    require_project(project_id)
    id = f"project-{project_id}" if project_id is not None else "common"
    with SessionLocal() as db:
        dashboard = db.get(Dashboard,id)
        if not dashboard or dashboard.connection_key != source_database_key():
            id = f"{source_database_key()[:32]}_{id}"
            dashboard = db.get(Dashboard,id)
        if not dashboard:
            initial = initial_dashboard(project_id)
            # Concurrent first visits create one board and both return it.
            # Never overwrite a board already created or edited by another request.
            db.execute(pg_insert(Dashboard).values(
                id=id, project_id=project_id, version=initial.version,
                cards=initial.cards, layout=initial.layout,
                connection_key=source_database_key(),
            ).on_conflict_do_nothing(index_elements=[Dashboard.id]))
            db.commit()
            dashboard = db.get(Dashboard, id)
        return dashboard_json(dashboard, db)


@app.put("/api/dashboards/{dashboard_id}")
def save_dashboard(dashboard_id:str,body:DashboardUpdate,user=Depends(require_editor)):
    card_ids = [card.id for card in body.cards]
    if len(card_ids)!=len(set(card_ids)) or len(body.layout)!=len(card_ids) or {p.i for p in body.layout}!=set(card_ids):
        raise HTTPException(422,"카드 ID와 배치 ID는 중복 없이 일치해야 합니다.")
    if any(item.x+item.w>12 for item in body.layout):
        raise HTTPException(422,"카드 배치가 12열 범위를 벗어났습니다.")
    with SessionLocal() as db:
        current = db.get(Dashboard,dashboard_id)
        if not current or current.connection_key != source_database_key():
            raise HTTPException(404,"대시보드를 찾을 수 없습니다.")
        require_project(current.project_id)
        hidden = hidden_card_ids(db, current.cards)
        if hidden.intersection(card_ids):
            raise HTTPException(422,"제외된 테이블의 카드 ID는 사용할 수 없습니다.")
        for card in body.cards:
            compiled = validate_card(card)
            card.dataset_version = compiled["dataset_version"]
            if compiled["project_id"] is not None and compiled["project_id"] != current.project_id:
                raise HTTPException(422,"프로젝트 전용 데이터셋은 해당 프로젝트 대시보드에서만 사용할 수 있습니다.")
        # Preserve hidden cards in storage so removing an ignore entry restores
        # them, even after the visible dashboard has been edited.
        cards = [c.model_dump() for c in body.cards] + [c for c in current.cards if c["id"] in hidden]
        layout = [l.model_dump() for l in body.layout] + [l for l in current.layout if l["i"] in hidden]
        if len(cards) > 40:
            raise HTTPException(422,"제외된 테이블의 보관된 카드를 포함해 최대 40개까지 저장할 수 있습니다.")
        result = db.execute(update(Dashboard).where(Dashboard.id==dashboard_id,Dashboard.version==body.version)
                            .values(cards=cards,layout=layout,version=body.version+1))
        if result.rowcount!=1:
            if not db.get(Dashboard,dashboard_id):
                raise HTTPException(404,"대시보드를 찾을 수 없습니다.")
            raise HTTPException(409,"다른 사용자가 수정했습니다. 새로고침 후 다시 적용하세요.")
        audit(db,user,"dashboard.update",dashboard_id,{"version":body.version+1})
        db.commit()
        return dashboard_json(db.get(Dashboard,dashboard_id), db)


@app.post("/api/query")
def query(body:QueryRequest,user=Depends(get_current_user)):
    return table_query(body.dataset_id,body.dataset_version,body.context)


@app.post("/api/cards/query")
def chart(body:ChartRequest,user=Depends(get_current_user)):
    return chart_response(body.card,body.context)


@app.post('/api/cards/python-script')
def card_python_script(body:ChartRequest,user=Depends(require_editor)):
    from .plotly_codegen import expanded_dataset_script, dataset_script
    from .query import scoped_context
    from .card_expressions import NUMERIC_TYPES
    require_project(body.context.project_id)
    if body.card.editor_mode == 'python' and body.card.script_input == 'dataset':
        compiled = validate_card(body.card)
        scoped_context(compiled['project_id'], body.context)
        baseline = ''
        try:
            simple = body.card.model_copy(update={'editor_mode':'simple'})
            validate_card(simple)
            baseline = dataset_script(simple.model_dump(), compiled['columns'])
        except (HTTPException, ValueError, TypeError, KeyError):
            pass  # A custom script need not have simple-editor axes or aggregates.
        return {'card':body.card.model_copy(update={'dataset_version':compiled['dataset_version']}).model_dump(),
                'simple_script':baseline, 'input_columns':[{'name':c['name'], 'description':c['name'], 'type':c['type']} for c in compiled['columns']]}
    simple = body.card.model_copy(update={'editor_mode':'simple'})
    if simple.kind == 'gantt':
        raise HTTPException(422, '간트 전용 편집기는 Python 변환을 지원하지 않습니다.')
    compiled = validate_card(simple)
    scoped_context(compiled['project_id'], body.context)
    numeric_color = simple.kind == 'scatter' and any(c['name']==simple.color and c['type'] in NUMERIC_TYPES for c in compiled['columns'])
    script, baseline = expanded_dataset_script(body.card.model_dump(), compiled['columns'], numeric_color=numeric_color)
    from .plotly_scripts import validate_script
    validate_script(script)
    converted = body.card.model_copy(update={'editor_mode':'python', 'script_input':'dataset',
        'python_script':script, 'dataset_version':compiled['dataset_version']})
    return {'card':converted.model_dump(), 'simple_script':baseline,
            'input_columns':[{'name':c['name'], 'description':c['name'], 'type':c['type']} for c in compiled['columns']]}


@app.post("/api/datasets/preview")
def dataset_preview(body:DatasetPreviewRequest,user=Depends(require_editor)):
    require_project(body.context.project_id)
    source = editor_source(body.spec.source_id)
    spec = simple_query(body.spec,source) if body.editor_mode == "simple" else body.spec
    result = preview_dataset(spec,body.context)
    result["simple_editor"] = simple_editor_state(result["canonical_sql"],source,spec.pivot)
    return result


def editor_source(source_id):
    with SessionLocal() as db:
        source = db.get(Source,source_id)
        if not source:
            raise HTTPException(404,"등록된 원천 테이블을 찾을 수 없습니다.")
        require_source_visible(source)
        return source_json(source)


def simple_query(spec, source):
    if spec.pivot is not None:
        from .pivot import pivot_definition
        return pivot_definition(spec, source)
    from .raw_sql import form_to_sql
    prepared = prepare_simple_spec(spec, source)
    # The editor's visible predicates are authoritative after initialization.
    return DatasetSpec(source_id=spec.source_id, raw_sql=form_to_sql(prepared,source))


@app.post("/api/datasets/simple-editor")
def dataset_simple_editor(body:SimpleEditorRequest,user=Depends(get_current_user)):
    return simple_editor_state(body.sql,editor_source(body.source_id),body.pivot)


@app.post('/api/sources/{source_id}/pivot-values')
def pivot_values(source_id:str, body:PivotValuesRequest, user=Depends(require_editor)):
    from .pivot import identifier
    source = editor_source(source_id)
    if body.column not in {c['name'] for c in source['columns']}:
        raise HTTPException(422, '원천 테이블의 컬럼을 선택하세요.')
    column = identifier(body.column)
    # Suggestions only. LIMIT bounds the response; values can also be entered
    # directly when a source has a large number of operation identifiers.
    sql = f"SELECT DISTINCT {column} FROM {identifier(source['schema_name'])}.{identifier(source['table_name'])} WHERE {column} IS NOT NULL LIMIT 51"
    with source_connection() as connection:
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
        values = [str(row[0]) for row in connection.exec_driver_sql(sql)]
    return {'values': sorted(values[:50]), 'truncated': len(values) > 50}


@app.post("/api/datasets/sql")
def dataset_sql(body:DatasetPreviewRequest,user=Depends(get_current_user)):
    require_project(body.context.project_id)
    source = editor_source(body.spec.source_id)
    supplied = simple_query(body.spec,source) if body.editor_mode == "simple" else body.spec
    spec, sql = normalize_definition(supplied,body.context.project_id)
    compiled = validate_dataset(spec)
    if body.context.project_id is not None:
        scope_note = "현재 프로젝트의 데이터만 서버에서 조회합니다. SQL에서 조건을 지우거나 다른 프로젝트를 지정해도 범위를 넓힐 수 없습니다. 공통 데이터는 전체 프로젝트에서 조회하세요."
    elif spec.raw_sql is not None:
        scope_note = "SQL 편집기에 표시된 조건으로 조회합니다."
    else:
        scope_note = "공통 원본을 조회합니다. 프로젝트 탭에서 저장하면 프로젝트 전용 쿼리 정의가 생성됩니다."
    sources = compiled.get("sources",[compiled["source"]] + [target for relation,target in compiled["relations"]])
    return {"sql":sql,"spec":spec.model_dump(),"source_tables":[f"{s['schema_name']}.{s['table_name']}" for s in sources],"project_id":body.context.project_id,"scope_note":scope_note,"simple_editor":simple_editor_state(sql,source,spec.pivot)}


@app.post("/api/datasets/group-completion")
def dataset_group_completion(body:GroupCompletionRequest,user=Depends(get_current_user)):
    # Advisory parsing only; preview/save still validate against the real catalog.
    return complete_groups(body.expressions,body.input_columns,body.having_sql)


@app.post("/api/datasets",status_code=201)
def dataset_create(body:DatasetCreate,user=Depends(require_editor)):
    require_project(body.project_id)
    preview = preview_dataset(body.spec,QueryContext(project_id=body.project_id))
    with SessionLocal() as db:
        dataset = DatasetVersion(id=str(uuid.uuid4()),version=1,name=body.name,project_id=body.project_id,spec=preview["spec"],columns=preview["columns"])
        db.add(dataset)
        audit(db,user,"dataset.create",dataset.id,{"name":body.name,"version":1,"project_id":body.project_id})
        db.commit()
        return dataset_json(dataset, catalog=TableVisibility.load(db))


@app.post('/api/gantt/dataset-template')
def gantt_dataset_template(body: ChartRequest, user=Depends(require_editor)):
    from .gantt_dataset import schedule_sql
    from .project_settings import managed_project
    from .query import scoped_context
    require_project(body.context.project_id)
    if body.card.kind != 'gantt' or not body.card.gantt or body.card.gantt.data_mode != 'tables':
        raise HTTPException(422, '원천 계획·현재 상태·TAT·이력 테이블을 선택하세요.')
    compiled = validate_card(body.card)
    context = scoped_context(compiled['project_id'], body.context)
    project = managed_project(context.project_id)
    sources = compiled['gantt_sources']
    return {'name': '간트 · 일정 통합', 'project_id': context.project_id,
            'spec': {'source_id': sources['plan'].id,
                     'raw_sql': schedule_sql(sources, lot_cds=project.lot_cds if project else (),
                                             started_column=body.card.gantt.started_column)}}


@app.post("/api/datasets/{dataset_id}/impact", response_model=DatasetImpact)
def preview_dataset_impact(dataset_id: str, body: DatasetUpdate, user=Depends(require_editor)):
    return dataset_impact(dataset_id, body)


@app.post("/api/datasets/{dataset_id}/versions",status_code=201)
def dataset_version(dataset_id:str,body:DatasetUpdate,user=Depends(require_editor)):
    require_project(body.project_id)
    with SessionLocal() as db:
        # Serialize versions of one logical dataset without locking unrelated datasets.
        lock_dataset_version(db, dataset_id)
        latest = db.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.id==dataset_id))
        if latest is None:
            raise HTTPException(404,"데이터셋을 찾을 수 없습니다.")
        if latest!=body.expected_version:
            raise HTTPException(409,"데이터셋에 새 버전이 있습니다. 최신 버전을 확인하세요.")
        previous = db.get(DatasetVersion,(dataset_id,latest))
        if not TableVisibility.load(db).allows_spec(previous.spec):
            raise HTTPException(404,"앱에서 제외된 테이블을 사용하는 데이터셋입니다.")
        if previous.project_id != body.project_id:
            raise HTTPException(422,"데이터셋의 저장 범위는 버전 변경으로 바꿀 수 없습니다. 프로젝트 전용 데이터셋으로 새로 저장하세요.")
        preview = preview_dataset(body.spec,QueryContext(project_id=previous.project_id))
        dataset = DatasetVersion(id=dataset_id,version=latest+1,name=body.name,project_id=previous.project_id,spec=preview["spec"],columns=preview["columns"])
        db.add(dataset)
        audit(db,user,"dataset.version",dataset_id,{"version":latest+1,"project_id":previous.project_id})
        db.commit()
        clear_cache()
        return dataset_json(dataset, catalog=TableVisibility.load(db))


@app.get("/api/datasets/{dataset_id}/versions")
def dataset_versions(dataset_id:str,project_id: int | None = None,user=Depends(get_current_user)):
    require_project(project_id)
    with SessionLocal() as db:
        values = list(db.scalars(select(DatasetVersion).where(DatasetVersion.id==dataset_id).order_by(DatasetVersion.version.desc())))
        if not values:
            raise HTTPException(404,"데이터셋을 찾을 수 없습니다.")
        if any(item.project_id is not None and item.project_id != project_id for item in values):
            raise HTTPException(404,"현재 프로젝트에서 사용할 수 없는 데이터셋입니다.")
        visibility = TableVisibility.load(db)
        visible = [dataset_json(item, catalog=visibility) for item in values if visibility.allows_spec(item.spec)]
        if not visible:
            raise HTTPException(404,"앱에서 제외된 테이블을 사용하는 데이터셋입니다.")
        return visible


@app.get("/api/datasets/{dataset_id}/latest")
def latest_dataset(dataset_id: str, project_id: int | None = None, user=Depends(get_current_user)):
    from .query import load_dataset
    require_project(project_id)
    dataset, catalog = load_dataset(dataset_id, with_catalog=True)
    if dataset.project_id is not None and dataset.project_id != project_id:
        raise HTTPException(404, "현재 프로젝트에서 사용할 수 없는 데이터셋입니다.")
    return dataset_json(dataset, catalog=catalog)


@app.get("/api/datasets/{dataset_id}/period-columns")
def dataset_period_columns(dataset_id: str, version: int | None = None, project_id: int | None = None, user=Depends(get_current_user)):
    from .dashboard_period import period_columns
    from .query import load_dataset, scoped_context
    dataset = load_dataset(dataset_id, version)
    scoped_context(dataset.project_id, QueryContext(project_id=project_id))
    return period_columns(validate_dataset(dataset.spec))


@app.get("/api/relations")
def relations(user=Depends(get_current_user)):
    with SessionLocal() as db:
        visibility = TableVisibility.load(db)
        return [relation_json(item) for item in db.scalars(select(Relation).order_by(Relation.id)) if item.id not in visibility.hidden_relations]


@app.get("/api/admin/users")
def users(user=Depends(require_admin)):
    with SessionLocal() as db:
        return [user_json(item) for item in db.scalars(select(User).order_by(User.username))]


@app.post("/api/admin/users",status_code=201)
def create_user(body:UserCreate,user=Depends(require_admin)):
    with SessionLocal() as db:
        lock_user_management(db,user)
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "identity:" + body.employee_id})
        if db.scalar(select(User).where(User.employee_id==body.employee_id)):
            raise HTTPException(409,"이미 등록된 사번입니다.")
        item = User(username="sso:"+body.employee_id,employee_id=body.employee_id,role=body.role,
                    active=body.active,profile_source="sso")
        db.add(item)
        db.flush()
        audit(db,user,"user.create",item.id,{"employee_id":body.employee_id,"role":body.role,"active":body.active})
        db.commit()
        return user_json(item)


@app.patch("/api/admin/users/{user_id}")
def update_user(user_id:str,body:UserUpdate,user=Depends(require_admin)):
    with SessionLocal() as db:
        lock_user_management(db,user)
        item = db.scalar(select(User).where(User.id==user_id).with_for_update())
        if not item:
            raise HTTPException(404,"사용자를 찾을 수 없습니다.")
        if item.profile_source!="sso" or item.employee_id is None:
            raise HTTPException(403,"테스트·이전 계정은 보존용입니다. SSO 사용자 권한만 관리할 수 있습니다.")
        if item.version!=body.expected_version:
            raise HTTPException(409,"다른 관리자가 변경했습니다. 사용자 목록을 새로고침해 주세요.")
        next_role = body.role if body.role is not None else item.role
        next_active = body.active if body.active is not None else item.active
        if item.id==user.id and (next_role!="admin" or not next_active):
            raise HTTPException(409,"본인의 권한이나 사용 상태는 다른 관리자가 변경해야 합니다.")
        if item.role=="admin" and item.active and item.employee_id is not None and item.profile_source=="sso" and (next_role!="admin" or not next_active):
            count = db.scalar(select(func.count()).select_from(User).where(User.role=="admin",User.active.is_(True),User.employee_id.is_not(None),User.profile_source=="sso"))
            if count<=1:
                raise HTTPException(409,"최소 한 명의 활성 SSO 관리자가 필요합니다.")
        before={"role":item.role,"active":item.active}
        after={"role":next_role,"active":next_active}
        if before!=after:
            item.role,item.active=next_role,next_active
            item.version+=1
            audit(db,user,"user.update",item.id,{"before":before,"after":after,"version":item.version})
        db.commit()
        return user_json(item)


@app.get("/api/admin/source-tables")
def source_tables(user=Depends(require_admin)):
    return discover_tables()


@app.post("/api/sources/discover")
def discover_dataset_sources(project_id: int | None = None,user=Depends(require_editor)):
    require_project(project_id)
    with SessionLocal() as db:
        sources = refresh_sources(db, project_id)
        db.commit()
        return sources


@app.get("/api/admin/sources")
def sources(user=Depends(require_admin)):
    with SessionLocal() as db:
        return [source_json(item) for item in db.scalars(select(Source).order_by(Source.id)) if source_visible(item)]


@app.post("/api/admin/sources",status_code=201)
def create_source(body:SourceCreate,user=Depends(require_admin)):
    with SessionLocal() as db:
        source = register_source(db,body)
        audit(db,user,"source.create",source.id,{"schema":body.schema_name,"table":body.table_name})
        db.commit()
        clear_cache()
        return source_json(source)


@app.get("/api/admin/relations")
def admin_relations(user=Depends(require_admin)):
    return relations(user)


@app.post("/api/admin/relations",status_code=201)
def create_relation(body:RelationCreate,user=Depends(require_admin)):
    with SessionLocal() as db:
        if db.get(Relation,body.id):
            raise HTTPException(409,"이미 등록된 관계 ID입니다.")
        relation = validate_relation(db,body)
        db.add(relation)
        audit(db,user,"relation.create",relation.id,body.model_dump())
        db.commit()
        return relation_json(relation)


@app.get("/api/admin/audit")
def audit_log(user=Depends(require_admin)):
    with SessionLocal() as db:
        return [{"id":row.id,"user_id":row.user_id,"action":row.action,"entity_id":row.entity_id,"details":row.details,"created_at":row.created_at.isoformat()} for row in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))]


from .assistant import router as assistant_router
from .rendering import router as rendering_router
app.include_router(assistant_router)
app.include_router(rendering_router)

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (DIST / "index.html").exists():
    @app.get("/{path:path}",include_in_schema=False)
    def frontend(path:str):
        if path=="api" or path.startswith("api/"):
            raise HTTPException(404,"API를 찾을 수 없습니다.")
        candidate = (DIST / path).resolve()
        if not candidate.is_relative_to(DIST.resolve()):
            raise HTTPException(404,"파일을 찾을 수 없습니다.")
        return FileResponse(candidate if candidate.is_file() else DIST/"index.html")
