"""DB-specific application projects; source databases are never modified."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlglot import exp

from .auth import require_admin
from .db import SessionLocal, source_database_key
from .models import AppProject, Audit, ProjectRemoval
from .schemas import StrictModel

router = APIRouter(prefix='/api/admin')


class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    lot_cds: list[str] = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    active: bool = True
    expected_version: int | None = None

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError('프로젝트 이름을 입력하세요.')
        return value.strip()

    @field_validator('lot_cds')
    @classmethod
    def codes(cls, values):
        result = list(dict.fromkeys(v.strip() for v in values))
        if any(not v or len(v) > 200 or '\x00' in v for v in result):
            raise ValueError('유효한 lot_cd를 입력하세요.')
        return result


def project_json(item):
    return dict(id=item.id, name=item.name, code=', '.join(item.lot_cds), lot_cds=item.lot_cds,
                description=item.description, active=item.active, version=item.version, managed=True)


def managed_project(project_id, *, db=None):
    if project_id is None:
        return None
    if db is None:
        with SessionLocal() as session:
            return managed_project(project_id, db=session)
    item = db.get(AppProject, project_id)
    if item is None and db.get(ProjectRemoval, project_id):
        raise HTTPException(404, '삭제된 프로젝트입니다.')
    if item and (item.connection_key != source_database_key() or not item.active):
        raise HTTPException(404, '현재 DB의 활성 프로젝트를 찾을 수 없습니다.')
    return item


def lot_predicate(codes, column='lot_cd'):
    col = exp.column(column)
    values = [exp.Literal.string(code) for code in codes]
    return exp.EQ(this=col, expression=values[0]) if len(values) == 1 else exp.In(this=col, expressions=values)


def managed_projects():
    with SessionLocal() as db:
        return [project_json(p) for p in db.scalars(select(AppProject).where(
            AppProject.connection_key == source_database_key(), AppProject.active.is_(True)).order_by(AppProject.id))]


@router.get('/projects')
def list_projects(user=Depends(require_admin)):
    with SessionLocal() as db:
        return [project_json(p) for p in db.scalars(select(AppProject).where(
            AppProject.connection_key == source_database_key()).order_by(AppProject.id))]


def save_project(body, user, project_id=None):
    if not source_database_key():
        raise HTTPException(422, '먼저 DB를 연결하세요.')
    from .source_discovery import refresh_sources
    from .query import clear_cache
    with SessionLocal() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(7126001903)'))
        item = db.get(AppProject, project_id) if project_id is not None else None
        if project_id is not None:
            if not item or item.connection_key != source_database_key():
                raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
            if body.expected_version != item.version:
                raise HTTPException(409, '프로젝트 설정이 변경되었습니다. 다시 불러오세요.')
        previous_codes = item.lot_cds if item else None
        if item is None:
            item = AppProject(**body.model_dump(exclude={'expected_version'}))
            db.add(item)
        else:
            for key, value in body.model_dump(exclude={'expected_version'}).items():
                setattr(item, key, value)
            item.version += 1
        try:
            db.flush()
            refresh_sources(db, item.id, project=item, previous_codes=previous_codes)
            db.add(Audit(user_id=user.id, action='project.update' if project_id else 'project.create',
                         entity_id=str(item.id), details={'name': item.name, 'lot_cds': item.lot_cds, 'active': item.active}))
            db.commit()
        except IntegrityError:
            raise HTTPException(409, '이 DB에 같은 이름의 프로젝트가 있습니다.') from None
        clear_cache()
        return project_json(item)


@router.post('/projects', status_code=201)
def create_project(body: ProjectInput, user=Depends(require_admin)):
    return save_project(body, user)


@router.put('/projects/{project_id}')
def update_project(project_id: int, body: ProjectInput, user=Depends(require_admin)):
    return save_project(body, user, project_id)


@router.delete('/projects/{project_id}')
def delete_project(project_id: int, expected_version: int = Query(ge=1), user=Depends(require_admin)):
    from .query import clear_cache
    with SessionLocal() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(7126001903)'))
        item = db.get(AppProject, project_id)
        if not item or item.connection_key != source_database_key():
            raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
        if expected_version != item.version:
            raise HTTPException(409, '프로젝트 설정이 변경되었습니다. 다시 불러오세요.')
        # Only application registration is removed. Source tables and saved project
        # definitions remain untouched; the tombstone rejects their old context.
        db.add(ProjectRemoval(id=item.id, connection_key=item.connection_key))
        db.add(Audit(user_id=user.id, action='project.delete', entity_id=str(item.id),
                     details={'name': item.name, 'lot_cds': item.lot_cds,
                              'description': item.description, 'active': item.active}))
        db.delete(item)
        db.commit()
    clear_cache()
    return {'deleted': True, 'project_id': project_id}


@router.get('/project-lot-codes')
def available_codes(user=Depends(require_admin)):
    from .source_discovery import database_tables
    from .query import source_connection
    from .expressions import quote
    tables = [s for s in database_tables() if 'lot_cd' in {c['name'] for c in s['columns']}]
    tables.sort(key=lambda s: (s['table_name'] not in ('lot_plan', 'current_status'), s['table_name']))
    values = set()
    with source_connection() as connection:
        connection.exec_driver_sql("SET LOCAL statement_timeout='5000ms'")
        for s in tables[:3]:
            table = f"{quote(s['schema_name'])}.{quote(s['table_name'])}"
            values.update(str(r[0]) for r in connection.execute(text(f'SELECT DISTINCT lot_cd FROM {table} WHERE lot_cd IS NOT NULL LIMIT 201')))
    return {'values': sorted(values)[:200], 'truncated': len(values) > 200}
