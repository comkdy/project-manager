"""Diagnostics must explain blockers without mutating workflows or invoking AI."""
from datetime import timedelta
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import event, select

from app import operations, worker_observation as observations
from app.analytics.models import AnalysisRun, AnalysisSchedule
from app.models import LlmCredential, User, utcnow
from app.recovery_contract import BackupView, RecoveryStatus
from app.semantic.collector_models import CollectionSettings, CollectedDraft, KnowledgeAnswer, KnowledgeQuestion
from test_analytics_service import env  # noqa: F401

BASE='/api/admin/operations'

@pytest.fixture
def system(env,monkeypatch):
    env.client.app.include_router(operations.router)
    with env.sessions() as db:LlmCredential.__table__.create(db.get_bind())
    env.probes=[]
    monkeypatch.setattr(operations,'check_connection',lambda:env.probes.append(True))
    monkeypatch.setattr(operations.llm_settings,'llm_configuration',lambda:('',''))
    monkeypatch.setattr(observations,'_states',{})
    monkeypatch.setattr(operations.recovery,'status',lambda user:RecoveryStatus(workspace_token='a:0',supported=True,reason='',busy=False,backup_count=0,can_create=True,items=[],notes=[]))
    return env

def items(system):
    response=system.client.get(BASE)
    assert response.status_code==200,response.text
    return {check['id']:check for check in response.json()['checks']}

@pytest.mark.parametrize('role',['editor','viewer'])
def test_only_active_admin_can_probe(system,role):
    system.actor.role=role
    assert system.client.get(BASE).status_code==403
    assert not system.probes

def test_live_revocation_is_checked_before_probing(system):
    with system.sessions.begin() as db:db.get(User,'admin').active=False
    assert system.client.get(BASE).status_code==403
    assert not system.probes

def test_read_only_empty_diagnostics_do_not_imply_all_ready(system):
    result=items(system)
    assert result['source']['status']=='ok'
    assert result['metadata']['status']=='ok'
    assert result['worker_analysis']['status']=='unknown'
    assert result['backup']['status']=='attention'
    assert result['off_device']['status']=='unknown'
    assert result['llm']['status']=='unknown'
    assert len(system.probes)==1
    with system.sessions() as db:
        assert list(db.scalars(select(AnalysisRun)))==[]
        assert list(db.scalars(select(AnalysisSchedule)))==[]
        assert list(db.scalars(select(CollectionSettings)))==[]

def test_jobs_are_counted_in_current_source_without_loading_results_or_changes(system):
    now=utcnow()
    with system.sessions.begin() as db:
        for identity,key,status,age in [('waiting','a','queued',6),('expired','a','running',1),('question','a','needs_input',0),('foreign','b','failed',0)]:
            db.add(AnalysisRun(id=identity,connection_key=key,kind='trend',status=status,request={},source_snapshot={},created_by='admin',created_at=now-timedelta(minutes=age),lease_until=now-timedelta(seconds=1)))
        db.add(KnowledgeQuestion(id='q',connection_key='a',fingerprint='q',title='Q',kind='metric',topic='test',dataset={},status='review'))
        db.flush()
        db.add(KnowledgeAnswer(id='answer',question_id='q',user_id='admin',text='not displayed',status='failed'))
        db.flush()
        db.add(CollectedDraft(id='draft',question_id='q',answer_id='answer',proposal={},original_proposal={},evidence={},status='pending'))
    statements=[]
    with system.sessions() as db:engine=db.get_bind()
    def observe(conn,cursor,statement,parameters,context,many):statements.append(statement)
    event.listen(engine,'before_cursor_execute',observe)
    try:result=items(system)
    finally:event.remove(engine,'before_cursor_execute',observe)
    assert result['analysis_queue']['status']=='attention'
    assert '5분 이상 대기 1건' in result['analysis_queue']['message']
    assert '실패 0건' in result['analysis_queue']['message']
    assert '기한 경과 1건' in result['analysis_queue']['message']
    assert result['knowledge_flow']['status']=='attention'
    assert '승인 대기 초안 1건' in result['knowledge_flow']['message']
    assert not any('app_analysis_runs.result' in statement for statement in statements)
    assert not any(statement.lstrip().upper().startswith(('UPDATE','INSERT','DELETE')) for statement in statements)
    with system.sessions() as db:
        assert db.get(AnalysisRun,'expired').status=='running'
        assert db.get(CollectedDraft,'draft').status=='pending'

def test_paused_other_source_and_overdue_schedules_have_distinct_guidance(system):
    now=utcnow()
    with system.sessions.begin() as db:
        for identity,key,enabled,next_run in [('paused','a',False,None),('due','a',True,now-timedelta(minutes=3)),('other','b',True,now-timedelta(days=1))]:
            db.add(AnalysisSchedule(id=identity,connection_key=key,created_by='admin',origin_run_id=identity,name=identity,request={},source_snapshot={},method_version='test',interval_hours=1,enabled=enabled,next_run_at=next_run))
    result=items(system)['schedules']
    assert result['status']=='attention'
    for phrase in ['일시정지 1개','시각 누락 1개','다른 DB 예약 1개']:assert phrase in result['message']
    with system.sessions() as db:assert db.get(AnalysisSchedule,'paused').enabled is False

def test_source_failure_does_not_hide_backup_or_expose_credentials(system,monkeypatch):
    def fail():raise HTTPException(503,'postgres://user:private-password@host')
    monkeypatch.setattr(operations,'check_connection',fail)
    result=items(system)
    assert result['source']['status']=='attention' and 'backup' in result
    assert 'private-password' not in json.dumps(result)

def backup_record(created,checked,status='verified'):
    return BackupView(id='a'*32,status=status,stage='done',created_at=created,checked_at=checked,
        size_bytes=123,table_count=30,row_count=1,checks=[],message='',can_verify=True,folder='test')

@pytest.mark.parametrize('age,checked_age,expected',[(1,1,'ok'),(8,0,'attention'),(-1,0,'attention')])
def test_backup_freshness_uses_creation_not_reverification(system,monkeypatch,age,checked_age,expected):
    now=utcnow();record=backup_record(now-timedelta(days=age),now-timedelta(days=checked_age))
    monkeypatch.setattr(operations.recovery,'status',lambda user:RecoveryStatus(workspace_token='a:0',supported=True,reason='',busy=False,backup_count=1,can_create=True,items=[record],notes=[]))
    assert items(system)['backup']['status']==expected
    assert items(system)['off_device']['status']=='unknown'

def test_backup_busy_unsupported_and_failed_read_are_not_ready(system,monkeypatch):
    state=RecoveryStatus(workspace_token='a:0',supported=True,reason='',busy=True,backup_count=0,can_create=False,items=[],notes=[])
    monkeypatch.setattr(operations.recovery,'status',lambda user:state)
    assert items(system)['backup']['status']=='info'
    state.supported=False
    assert items(system)['backup']['status']=='unknown'
    def fail(user):raise HTTPException(503,'private-path')
    monkeypatch.setattr(operations.recovery,'status',fail)
    result=items(system)
    assert result['backup']['status']=='unknown' and 'private-path' not in json.dumps(result)

def test_ai_only_reads_current_users_key_and_never_returns_it(system,monkeypatch):
    from cryptography.fernet import Fernet
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(operations.llm_settings,'llm_configuration',lambda:('https://local.example/v1','test'))
    monkeypatch.setattr(operations.llm_settings,'cipher',lambda:cipher)
    with system.sessions.begin() as db:
        db.add(LlmCredential(user_id='reader',encrypted_key='cannot-decrypt'))
    assert items(system)['llm']['status']=='unknown'
    with system.sessions.begin() as db:
        db.add(LlmCredential(user_id='admin',encrypted_key=cipher.encrypt(json.dumps({'user_id':'admin','key':'private-key'}).encode()).decode(),checked_at=utcnow(),check_status='connected',checked_config=operations.llm_settings.config_fingerprint('https://local.example/v1','test')))
    result=items(system)
    assert result['llm']['status']=='info' and result['llm']['observed_at']
    assert 'private-key' not in json.dumps(result) and 'local.example' not in json.dumps(result)
    monkeypatch.setattr(operations.llm_settings,'cipher',lambda:Fernet(Fernet.generate_key()))
    assert items(system)['llm']['status']=='attention'

def test_worker_observation_preserves_return_and_failure(monkeypatch):
    monkeypatch.setattr(observations,'_states',{})
    observations.start('analysis')
    sentinel=object()
    assert observations.run('analysis',lambda:sentinel) is sentinel
    assert observations.snapshot()[0]['status']=='ok'
    def fail():raise ValueError('private exception')
    with pytest.raises(ValueError):observations.run('analysis',fail)
    result=observations.snapshot()[0]
    assert result['status']=='attention' and 'private' not in str(result)
    observations.run('analysis',lambda:None)
    assert observations.snapshot()[0]['status']=='ok'

def test_worker_staleness_and_late_completion_after_restart(monkeypatch):
    monkeypatch.setattr(observations,'_states',{})
    clock=[0];monkeypatch.setattr(observations.time,'monotonic',lambda:clock[0])
    observations.start('analysis');token=observations.begin('analysis')
    assert observations.snapshot()[0]['status']=='info'
    clock[0]=1201
    assert observations.snapshot()[0]['status']=='attention'
    observations.stop('analysis');observations.finish('analysis',token,True)
    assert observations.snapshot()[0]['status']=='attention'
    observations.start('analysis');observations.finish('analysis',token,True)
    assert observations.snapshot()[0]['status']=='info'
    observations.run('analysis',lambda:None);clock[0]+=31
    assert observations.snapshot()[0]['status']=='attention'
