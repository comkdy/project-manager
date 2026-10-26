import logging

from sqlalchemy import select, text

from .auth import LOCAL_ADMIN_USERNAME, employee_id
from .catalog import discover_tables, projects_list, register_source, validate_relation
from .db import Base, SessionLocal, engine, settings
from .models import Dashboard, DatabaseMount, DatasetVersion, Relation, Source, User
from fastapi import HTTPException
from .identity_migration import migrate_identity
from .dataset_migration import migrate_dataset_scope
from .source_migration import migrate_source_connections
from .semantic.migration import migrate_semantic
from .query import source_connection
from .schemas import CardSpec, Filter, RelationCreate, SourceCreate
from .table_visibility import TableVisibility, source_visible

logger = logging.getLogger(__name__)
SOURCE_NAMES = {
    "projects": "프로젝트", "lots": "Lot", "wafers": "Wafer", "equipment": "장비",
    "inline_measurements": "Inline 측정", "wat_measurements": "WAT 측정",
    "die_results": "EDS Die 결과", "wafer_summary": "Wafer 요약", "wafer_correlation": "Inline–WAT 상관관계",
}


def initial_cards(project_id=None):
    if not settings.demo_mode:
        return []
    with SessionLocal() as db:
        visibility = TableVisibility.load(db)
        ids = {d.id for d in db.scalars(select(DatasetVersion).where(DatasetVersion.version == 1)) if visibility.allows_spec(d.spec)}
    cards = []
    def add(id, title, dataset, kind, **kwargs):
        if dataset in ids:
            cards.append(CardSpec(id=id, title=title, dataset_id=dataset, dataset_version=1, kind=kind, **kwargs).model_dump())
    add("yield", "전체 수율", "wafer_summary", "kpi", aggregate="yield")
    add("wafers", "분석 Wafer", "wafer_summary", "kpi", aggregate="count")
    add("project-yield", "프로젝트별 수율", "wafer_summary", "bar", x="project_name", y="yield_pct", aggregate="yield")
    add("lot-yield", "Lot별 수율 추이", "wafer_summary", "line", x="lot_code", y="yield_pct", aggregate="yield")
    add("inline-wat", "Inline–WAT 상관관계", "wafer_summary", "scatter", x="inline_thickness", y="wat_vth", color="project_name")
    add("equipment", "장비별 Inline 두께 분포", "inline_measurements", "box", x="equipment_id", y="value", filters=[Filter(column="metric_name",op="eq",value="thickness"),Filter(column="process_step",op="eq",value="DEP")])
    if "die_results" in ids:
        with source_connection() as connection:
            first = connection.scalar(text("SELECT MIN(wafer_id) FROM public.die_results" + (" WHERE project_id=:p" if project_id is not None else "")), {"p": project_id})
        if first is not None:
            add("wafer-map", "Wafer Map · 검사 수율", "die_results", "wafer", x="die_x", y="die_y", filters=[Filter(column="wafer_id",op="eq",value=first)])
    return cards


def initial_dashboard(project_id=None):
    cards = initial_cards(project_id)
    positions = [(0,0,3,3),(3,0,3,3),(6,0,6,3),(0,3,6,5),(6,3,6,5),(0,8,6,5),(6,8,6,5)]
    layout = [{"i": card["id"], "x": pos[0], "y": pos[1], "w": pos[2], "h": pos[3]} for card,pos in zip(cards,positions)]
    return Dashboard(id=f"project-{project_id}" if project_id is not None else "common", project_id=project_id,version=1,cards=cards,layout=layout)


def initialize():
    if engine is None:
        logger.warning("APP_DATABASE_URL is not configured; API database operations are unavailable")
        return
    Base.metadata.create_all(engine)
    migrate_identity(engine)
    migrate_dataset_scope(engine)
    migrate_source_connections(engine)
    migrate_semantic(engine)
    with SessionLocal() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(7126001902)"))
        if settings.auth_mode == "local_admin" and settings.demo_mode:
            if not db.scalar(select(User).where(User.username == LOCAL_ADMIN_USERNAME)):
                db.add(User(username=LOCAL_ADMIN_USERNAME, name="테스트 관리자", team="", role="admin",
                            active=True, profile_source="local", employee_id=None))
        if settings.sso_bootstrap_admin_employee_id.strip():
            identity = employee_id(settings.sso_bootstrap_admin_employee_id.strip())
            if not db.scalar(select(User).where(User.employee_id == identity)):
                # Pre-authorize only. A real verified SSO cookie is still mandatory.
                db.add(User(username="sso:" + identity, employee_id=identity, role="admin",
                            active=True, profile_source="sso"))
        db.commit()
        # Saved DB selections have their own workspace. Demo seeding must never
        # query a newly mounted corporate database or block recovery settings.
        if db.get(DatabaseMount, 1):
            return
    if not settings.demo_mode:
        return
    try:
        tables = {(t["schema_name"], t["table_name"]) for t in discover_tables()}
    except HTTPException:
        logger.warning("Source unavailable; configure a connection in DB settings")
        return
    with SessionLocal() as db:
        for table, name in SOURCE_NAMES.items():
            if ("public", table) in tables and not db.get(Source, table):
                project_column = "id" if table == "projects" else None if table == "equipment" else "project_id"
                register_source(db, SourceCreate(id=table,name=name,schema_name="public",table_name=table,project_column=project_column,primary_key="id"))
        db.commit()
        for left, right, col in [("inline_measurements","equipment","equipment_id"),("wat_measurements","equipment","equipment_id"),
                                  ("inline_measurements","wafers","wafer_id"),("wat_measurements","wafers","wafer_id"),("die_results","wafers","wafer_id")]:
            id = f"{left}_{right}"
            left_source, right_source = db.get(Source,left), db.get(Source,right)
            if left_source and right_source and source_visible(left_source) and source_visible(right_source) and not db.get(Relation,id):
                relation = validate_relation(db,RelationCreate(id=id,left_source_id=left,right_source_id=right,left_column=col,right_column="id"))
                db.add(relation)
        db.commit()
    for project_id in [None] + [p["id"] for p in projects_list()]:
        with SessionLocal() as db:
            id = f"project-{project_id}" if project_id is not None else "common"
            if not db.get(Dashboard,id):
                db.add(initial_dashboard(project_id))
                db.commit()
