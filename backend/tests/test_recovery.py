"""Admission, retry and filesystem safety without accessing a real database."""
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.engine import make_url
from app import recovery, db as database
from app.models import User
from test_analytics_service import env  # noqa: F401

BASE='/api/admin/recovery'
REAL_WORK=recovery.work

@pytest.fixture
def system(env, monkeypatch, tmp_path):
    env.client.app.include_router(recovery.router)
    monkeypatch.setattr(recovery,'ROOT',tmp_path)
    monkeypatch.setattr(recovery,'BACKUP_ROOT',tmp_path/'backups')
    monkeypatch.setattr(database.settings,'app_database_url','postgresql://local@127.0.0.1/test')
    monkeypatch.setattr(recovery,'configuration',lambda:(make_url(database.settings.app_database_url),{}))
    locks=[]
    def acquire(url):
        lock=SimpleNamespace(closed=False)
        lock.close=lambda:setattr(lock,'closed',True)
        locks.append(lock);return lock
    monkeypatch.setattr(recovery,'acquire',acquire)
    jobs=[]
    def work(record,url,runtime,lock,create_new):
        jobs.append((record['id'],create_new))
        record.update(status='verified',sha256='fake-test-hash')
        recovery.write_record(record);lock.close()
    monkeypatch.setattr(recovery,'work',work)
    env.jobs=jobs;env.locks=locks
    return env

def body():return dict(workspace_token=database.workspace_token(),request_id=uuid.uuid4().hex)

@pytest.mark.parametrize('role',['viewer','editor'])
def test_admin_only_for_list_backup_and_verify(system,role):
    system.actor.role=role
    for method,path,payload in [('get',BASE,None),('post',BASE,body()),('post',BASE+'/'+uuid.uuid4().hex+'/verify',body())]:
        response=getattr(system.client,method)(path,**({'json':payload} if payload else {}))
        assert response.status_code==403
    assert not system.jobs

def test_live_permission_revocation_and_workspace_switch(system):
    payload=body();payload['workspace_token']='different:workspace'
    assert system.client.post(BASE,json=payload).status_code==409
    with system.sessions.begin() as db:db.get(User,'admin').role='viewer'
    assert system.client.post(BASE,json=body()).status_code==403
    assert system.client.get(BASE).status_code==403
    assert not system.jobs

def test_request_ledger_survives_reverification_and_rejects_cross_operation_reuse(system):
    original=body();first=system.client.post(BASE,json=original)
    assert first.status_code==202
    identity=first.json()['id'];verify=BASE+'/'+identity+'/verify';again=body()
    assert system.client.post(verify,json=again).status_code==202
    assert system.client.post(BASE,json=original).json()['id']==identity
    assert system.client.post(verify,json=again).json()['id']==identity
    assert len(system.jobs)==2
    assert system.client.post(verify,json=original).status_code==422
    assert system.client.post(BASE,json=again).status_code==422
    assert all(lock.closed for lock in system.locks)

def test_busy_limit_and_interrupted_status(system,monkeypatch):
    identity=system.client.post(BASE,json=body()).json()['id']
    record=recovery.records()[0];record['status']='running';recovery.write_record(record)
    assert system.client.get(BASE).json()['items'][0]['status']=='interrupted'
    monkeypatch.setattr(recovery,'acquire',lambda url:None)
    data=system.client.get(BASE).json()
    assert data['busy'] and not data['can_create'] and not data['items'][0]['can_verify']
    assert system.client.post(BASE,json=body()).status_code==409
    monkeypatch.setattr(recovery,'acquire',lambda url:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(recovery,'LIMIT',1)
    assert system.client.post(BASE,json=body()).status_code==409
    # Reverification is still possible at the retention limit.
    assert system.client.post(BASE+'/'+identity+'/verify',json=body()).status_code==202

def test_new_job_persists_old_interruption(system):
    system.client.post(BASE,json=body())
    record=recovery.records()[0];record['status']='running';recovery.write_record(record)
    system.client.post(BASE,json=body())
    old=next(r for r in recovery.records() if r['id']==record['id'])
    assert old['status']=='interrupted'

def test_unsupported_config_and_malformed_record_fail_closed(system,monkeypatch):
    def unsupported():raise recovery.RecoveryError('unsupported')
    monkeypatch.setattr(recovery,'configuration',unsupported)
    assert system.client.get(BASE).json()['supported'] is False
    assert system.client.post(BASE,json=body()).status_code==422
    monkeypatch.setattr(recovery,'configuration',lambda:(None,{}))
    root=recovery.folder(uuid.uuid4().hex);root.mkdir(parents=True)
    (root/'record.json').write_text('not json')
    response=system.client.get(BASE)
    assert response.status_code==503 and 'not json' not in response.text

@pytest.mark.parametrize('identity',['../outside','..','A'*32,'x'*32,''])
def test_backup_identity_cannot_escape_storage(system,identity):
    with pytest.raises(recovery.RecoveryError):recovery.folder(identity)

def test_configured_backup_directory_cannot_escape_workspace(system,monkeypatch):
    monkeypatch.setattr(recovery,'BACKUP_ROOT',recovery.ROOT.parent/'outside-workspace')
    with pytest.raises(recovery.RecoveryError):recovery.folder(uuid.uuid4().hex)

def test_tamper_is_rejected_before_creating_any_temporary_database(system,monkeypatch):
    record={'id':uuid.uuid4().hex,'sha256':'unexpected'}
    root=recovery.folder(record['id']);root.mkdir(parents=True)
    (root/'metadata.dump').write_bytes(b'tampered')
    monkeypatch.setattr(recovery,'admin_connect',lambda _:pytest.fail('No DB may be created for a corrupt archive'))
    with pytest.raises(recovery.RecoveryError,match='무결성'):recovery.restore_check(record,None,{})

def test_failure_always_releases_lock_and_redacts_exception(system,monkeypatch):
    def broken(*args):raise RuntimeError('password=secret must not be returned')
    monkeypatch.setattr(recovery,'backup',broken)
    monkeypatch.setattr(recovery,'cleanup',lambda *args:None)
    record=dict(id=uuid.uuid4().hex,database_key=recovery.storage().name,created_at='2026-10-24T00:00:00Z')
    lock=SimpleNamespace(closed=False);lock.close=lambda:setattr(lock,'closed',True)
    REAL_WORK(record,None,{},lock,True)
    assert lock.closed and record['status']=='failed'
    assert 'secret' not in json.dumps(record)

def test_cleanup_never_drops_an_unmarked_or_foreign_database(system,monkeypatch):
    calls=[]
    class Admin:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,args=None):calls.append(str(query));return self
        def fetchone(self):return ('another-tool','wafer_metadata')
    monkeypatch.setattr(recovery,'admin_connect',lambda _:Admin())
    with pytest.raises(recovery.RecoveryError):
        recovery.cleanup({},dict(id=uuid.uuid4().hex,temporary_database='restorecheck_'+uuid.uuid4().hex))
    assert not any('DROP' in query for query in calls)

@pytest.mark.parametrize('change',[
    {'host':'remote.example'}, {'port':5432}, {'username':'postgres'},
    {'database':'production'}, {'query':{'sslmode':'disable'}},
])
def test_configuration_refuses_unmanaged_database_targets(monkeypatch,tmp_path,change):
    monkeypatch.setattr(recovery,'ROOT',tmp_path)
    runtime={'marker':'wafer-local-demo-v1','port':55432,'bin_dir':str(tmp_path/'.local/pgsql/bin'),'data_dir':str(tmp_path/'.local/pgdata')}
    (tmp_path/'.local').mkdir()
    (tmp_path/'.local/runtime.json').write_text(json.dumps(runtime))
    valid=make_url('postgresql+psycopg://wafer_metadata@127.0.0.1:55432/wafer_app')
    monkeypatch.setattr(database.settings,'app_database_url',valid.set(**change).render_as_string())
    monkeypatch.setattr(database.settings,'demo_mode',True)
    monkeypatch.setattr(database.settings,'auth_mode','local_admin')
    with pytest.raises(recovery.RecoveryError):recovery.configuration()

def test_credential_validation_reports_missing_keys_without_revealing_secrets(monkeypatch):
    from cryptography.fernet import Fernet
    from app import credential_store,llm_settings
    key=Fernet(Fernet.generate_key())
    monkeypatch.setattr(credential_store,'cipher',lambda:key)
    monkeypatch.setattr(llm_settings,'cipher',lambda:key)
    class Connection:
        def execute(self,query):self.llm='llm' in query;return self
        def fetchall(self):
            plain=json.dumps({'user_id':'owner','key':'private-test-token'}) if self.llm else 'postgresql://owner:private-pass@localhost/test'
            return [('owner',key.encrypt(plain.encode()).decode())]
    checks=recovery.credential_checks(Connection())
    assert all(check['status']=='passed' for check in checks)
    monkeypatch.setattr(llm_settings,'cipher',lambda:Fernet(Fernet.generate_key()))
    checks=recovery.credential_checks(Connection())
    assert checks[0]['status']=='passed' and checks[1]['status']=='attention'
    assert 'private-' not in json.dumps(checks)
