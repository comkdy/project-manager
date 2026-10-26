import uuid
import pytest
from sqlalchemy import delete, select
from app.db import SessionLocal, source_database_key
from app.models import AppProject, ProjectRemoval, DatasetVersion, Dashboard, Audit
from app import source_discovery
from test_source_discovery import other_database, table
from test_project_datasets import api


@pytest.fixture
def project_db(api,other_database):
    other_database.setattr(source_discovery,'database_tables',lambda:[table('public','lot_plan',('lot_cd','lot_id','operation_seq','operation'),'')])
    key=source_database_key()
    yield
    with SessionLocal.begin() as db:
        ids=list(db.scalars(select(AppProject.id).where(AppProject.connection_key==key)))
        ids+=list(db.scalars(select(ProjectRemoval.id).where(ProjectRemoval.connection_key==key)))
        db.execute(delete(DatasetVersion).where(DatasetVersion.project_id.in_(ids)))
        db.execute(delete(Audit).where(Audit.action.like('project.%'),Audit.entity_id.in_([str(i) for i in ids])))
        db.execute(delete(AppProject).where(AppProject.connection_key==key))
        db.execute(delete(ProjectRemoval).where(ProjectRemoval.connection_key==key))


def test_delete_registration_preserves_definitions_and_rejects_old_context(api,project_db):
    body=dict(name='Remove '+uuid.uuid4().hex[:8],lot_cds=['XXXA'])
    p=api.post('/api/admin/projects',json=body).json();pid=p['id']
    dataset=api.get(f'/api/bootstrap?project_id={pid}').json()['datasets'][0]
    board=api.get(f'/api/dashboards?project_id={pid}').json()
    shared=api.get('/api/bootstrap').json()['datasets']
    assert api.delete(f'/api/admin/projects/{pid}?expected_version=2').status_code==409
    assert api.delete(f'/api/admin/projects/{pid}?expected_version=1').status_code==200
    assert p['id'] not in [v['id'] for v in api.get('/api/admin/projects').json()]
    assert api.get(f'/api/bootstrap?project_id={pid}').status_code==404
    assert api.get(f'/api/dashboards?project_id={pid}').status_code==404
    assert api.put('/api/dashboards/'+board['id'],json={k:board[k] for k in ('version','cards','layout')}).status_code==404
    assert api.post('/api/query',json={'dataset_id':dataset['id'],'dataset_version':1}).status_code==404
    assert api.delete(f'/api/admin/projects/{pid}?expected_version=1').status_code==404
    assert api.get('/api/bootstrap').json()['datasets']==shared
    with SessionLocal() as db:
        assert db.get(AppProject,pid) is None
        assert db.get(ProjectRemoval,pid) is not None
        assert db.get(DatasetVersion,(dataset['id'],1)).spec==dataset['spec']
        assert db.get(Dashboard,board['id']) is not None
    replacement=api.post('/api/admin/projects',json=body)
    assert replacement.status_code==201 and replacement.json()['id']!=pid


def test_delete_rejects_other_db_and_non_admin(api,project_db):
    from app.auth import require_admin
    from app.main import app
    from fastapi import HTTPException
    body=dict(name='Boundary '+uuid.uuid4().hex[:8],lot_cds=['XXXA'])
    p=api.post('/api/admin/projects',json=body).json();pid=p['id']
    with SessionLocal.begin() as db:
        item=db.get(AppProject,pid);key=item.connection_key;item.connection_key='other-db'
    try:
        assert api.delete(f'/api/admin/projects/{pid}?expected_version=1').status_code==404
    finally:
        with SessionLocal.begin() as db:db.get(AppProject,pid).connection_key=key
    def forbidden():raise HTTPException(403,'admin required')
    app.dependency_overrides[require_admin]=forbidden
    try:assert api.delete(f'/api/admin/projects/{pid}?expected_version=1').status_code==403
    finally:app.dependency_overrides.pop(require_admin)
    with SessionLocal() as db:assert db.get(AppProject,pid) is not None


def test_single_multi_filters_versions_and_disabled_preservation(api,project_db):
    body=dict(name='QA '+uuid.uuid4().hex[:6],lot_cds=['XXXA'],description='Scope test',active=True)
    created=api.post('/api/admin/projects',json=body)
    assert created.status_code==201,created.text
    p=created.json();pid=p['id']
    initial=api.get(f'/api/bootstrap?project_id={pid}').json()
    dataset,=initial['datasets']
    assert dataset['project_id']==pid and "WHERE lot_cd = 'XXXA'" in ' '.join(dataset['spec']['raw_sql'].split())
    assert len(api.get('/api/bootstrap').json()['datasets'])==1
    updated=api.put(f'/api/admin/projects/{pid}',json=dict(body,lot_cds=['XXXA','XXXB'],expected_version=p['version']))
    assert updated.status_code==200,updated.text
    current=api.get(f'/api/bootstrap?project_id={pid}').json()['datasets'][0]
    assert current['id']==dataset['id'] and current['version']==2
    assert "lot_cd IN ('XXXA', 'XXXB')" in current['spec']['raw_sql']
    assert api.put(f'/api/admin/projects/{pid}',json=dict(body,expected_version=1)).status_code==409
    disabled=api.put(f'/api/admin/projects/{pid}',json=dict(body,lot_cds=['XXXA','XXXB'],active=False,expected_version=2))
    assert disabled.status_code==200
    assert api.get(f'/api/bootstrap?project_id={pid}').status_code==404
    assert not api.get('/api/bootstrap').json()['projects']
    with SessionLocal() as db:assert db.get(DatasetVersion,(current['id'],2)) is not None
    assert api.put(f'/api/admin/projects/{pid}',json=dict(body,lot_cds=['XXXA','XXXB'],expected_version=3)).status_code==200
    assert api.get(f'/api/bootstrap?project_id={pid}').json()['datasets'][0]['version']==2


def test_project_auto_columns_refresh_after_common_discovery(api,project_db,other_database):
    columns=['lot_cd','lot_id','operation_seq','operation']
    other_database.setattr(source_discovery,'database_tables',lambda:[table('public','lot_plan',columns,'')])
    p=api.post('/api/admin/projects',json=dict(name='Schema '+uuid.uuid4().hex[:8],lot_cds=['XXXA'])).json()
    suffix=f"?project_id={p['id']}"
    before=api.get('/api/bootstrap'+suffix).json()['datasets'][0]
    columns+=['main_oper_yn','area_id','samp_seq','skip_yn']
    api.get('/api/bootstrap')  # Common discovery updates the source first.
    after=api.get('/api/bootstrap'+suffix).json()['datasets'][0]
    assert after['id']==before['id'] and after['version']==before['version']+1
    assert [c['name'] for c in after['columns']]==columns
    assert "WHERE lot_cd = 'XXXA'" in ' '.join(after['spec']['raw_sql'].split())
    assert api.get('/api/bootstrap'+suffix).json()['datasets'][0]['version']==after['version']


def test_literal_codes_and_authored_query_are_preserved(api,project_db):
    code="X' OR true --"
    body=dict(name='Literal '+uuid.uuid4().hex[:8],lot_cds=[code])
    p=api.post('/api/admin/projects',json=body).json()
    dataset=api.get(f"/api/bootstrap?project_id={p['id']}").json()['datasets'][0]
    from sqlglot import parse_one, exp
    sql=dataset['spec']['raw_sql']
    assert parse_one(sql,read='postgres').args['where'].this.expression==exp.Literal.string(code)
    authored=sql+' AND operation_seq > 2'
    with SessionLocal.begin() as db:
        current=db.get(DatasetVersion,(dataset['id'],1))
        current.spec={**current.spec,'raw_sql':authored}
    assert api.put(f"/api/admin/projects/{p['id']}",json=dict(body,lot_cds=['XXXC'],expected_version=1)).status_code==200
    after=api.get(f"/api/bootstrap?project_id={p['id']}").json()['datasets'][0]
    assert after['version']==1 and after['spec']['raw_sql']==authored
