from fastapi import HTTPException
from sqlalchemy import select, text

from .db import SessionLocal
from .expressions import quote
from .models import DatasetVersion, Relation, Source
from .query import source_connection, source_json
from .schemas import DatasetSpec, RelationCreate, SourceCreate
from .table_visibility import require_source_visible, source_visible, table_ignored

SAFE_COLUMN_TYPES = {"smallint", "integer", "bigint", "numeric", "real", "double precision", "text",
                     "character varying", "character", "boolean", "date", "timestamp without time zone",
                     "timestamp with time zone", "uuid"}


def discover_tables():
    with source_connection() as connection:
        return [dict(row) for row in connection.execute(text("""
            SELECT table_schema AS schema_name, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog','information_schema')
            ORDER BY table_schema, table_name
        """)).mappings() if not table_ignored(row["schema_name"], row["table_name"])]


def inspect_source(body: SourceCreate):
    quote(body.schema_name), quote(body.table_name), quote(body.primary_key)
    require_source_visible(body)
    if body.schema_name.startswith("pg_") or body.schema_name == "information_schema":
        raise HTTPException(422, "시스템 스키마는 등록할 수 없습니다.")
    with source_connection() as connection:
        columns = [dict(row) for row in connection.execute(text("""
            SELECT column_name AS name, data_type AS type
            FROM information_schema.columns
            WHERE table_schema=:schema AND table_name=:table
            ORDER BY ordinal_position
        """), {"schema": body.schema_name, "table": body.table_name}).mappings()]
        if not columns:
            raise HTTPException(404, "테이블을 찾을 수 없거나 조회 권한이 없습니다.")
        available = {c["name"] for c in columns}
        if body.columns is not None:
            if not set(body.columns).issubset(available) or len(set(body.columns)) != len(body.columns):
                raise HTTPException(422, "등록할 컬럼 목록을 확인하세요.")
            columns = [c for c in columns if c["name"] in body.columns]
        selected = {c["name"] for c in columns}
        if body.primary_key not in selected or (body.project_column and body.project_column not in selected):
            raise HTTPException(422, "고유 키와 프로젝트 컬럼은 등록 컬럼에 포함되어야 합니다.")
        if any(c["type"] not in SAFE_COLUMN_TYPES for c in columns):
            raise HTTPException(422, "지원하는 스칼라 타입의 컬럼만 선택하세요. 배열·JSON·사용자 정의 타입은 제외하세요.")
        table = f"{quote(body.schema_name)}.{quote(body.table_name)}"
        pk = quote(body.primary_key)
        # Views have no catalog primary-key constraint, so verify their actual key too.
        invalid = connection.scalar(text(f"SELECT 1 FROM {table} GROUP BY {pk} HAVING {pk} IS NULL OR COUNT(*) > 1 LIMIT 1"))
        if invalid:
            raise HTTPException(422, "고유 키 컬럼에 NULL 또는 중복 값이 있습니다.")
    return columns


def register_source(db, body: SourceCreate):
    if db.get(Source, body.id):
        raise HTTPException(409, "이미 등록된 원본 ID입니다.")
    columns = inspect_source(body)
    source = Source(id=body.id, name=body.name, schema_name=body.schema_name, table_name=body.table_name,
                    project_column=body.project_column, primary_key=body.primary_key, columns=columns)
    db.add(source)
    if not db.get(DatasetVersion, (body.id, 1)):
        db.add(DatasetVersion(id=body.id, name=body.name, version=1,
                              spec=DatasetSpec(source_id=body.id).model_dump(), columns=columns))
    db.flush()
    return source


def validate_relation(db, body: RelationCreate):
    left, right = db.get(Source, body.left_source_id), db.get(Source, body.right_source_id)
    if not left or not right:
        raise HTTPException(404, "관계의 원본을 먼저 등록하세요.")
    require_source_visible(left)
    require_source_visible(right)
    if body.left_source_id == body.right_source_id:
        raise HTTPException(422, "첫 버전에서는 자기 참조 관계를 지원하지 않습니다.")
    left_types, right_types = {c["name"]: c["type"] for c in left.columns}, {c["name"]: c["type"] for c in right.columns}
    if body.left_column not in left_types or body.right_column not in right_types:
        raise HTTPException(422, "관계 컬럼이 등록되어 있지 않습니다.")
    if body.right_column != right.primary_key:
        raise HTTPException(422, "다대일 관계는 오른쪽 원본의 등록된 고유 키만 참조할 수 있습니다.")
    numeric = {"smallint", "integer", "bigint", "numeric", "real", "double precision"}
    if left_types[body.left_column] != right_types[body.right_column] and not {left_types[body.left_column], right_types[body.right_column]}.issubset(numeric):
        raise HTTPException(422, "관계 컬럼의 타입이 호환되지 않습니다.")
    # A shared dimension may be joined to project facts; project facts must not be
    # joined from a shared source because that would break the one-row identity.
    if not left.project_column and right.project_column:
        raise HTTPException(422, "공통 원본에서 프로젝트별 원본으로의 관계는 지원하지 않습니다.")
    return Relation(**body.model_dump())


def relation_json(item):
    return {k: getattr(item, k) for k in ("id", "left_source_id", "right_source_id", "left_column", "right_column", "join_type")}


def projects_list():
    from .project_settings import managed_projects
    managed = managed_projects()
    with SessionLocal() as db:
        source = db.get(Source, "projects")
        if not source or not source_visible(source):
            return managed
        cols = {c["name"] for c in source.columns}
        if not {"id", "name", "code"}.issubset(cols):
            return managed
        table = f"{quote(source.schema_name)}.{quote(source.table_name)}"
    with source_connection() as connection:
        return [dict(row) for row in connection.execute(text(f"SELECT id,name,code FROM {table} ORDER BY id LIMIT 500")).mappings()] + managed
