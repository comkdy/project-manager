"""Real schedule APIs, queue, controls and evidence with disposable metadata."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import activity, db as database
from app.analytics import schedules, service, worker
from app.analytics.models import AnalysisNotification, AnalysisRun, AnalysisSchedule
from app.analytics.schemas import AnalysisRequest
from app.models import Source, User, utcnow
from test_analytics_service import env, measurement, submit, trend_request

BASE = '/api/analytics/schedules'


@pytest.fixture(autouse=True)
def routes(env):
    env.client.app.include_router(schedules.router)
    env.client.app.include_router(activity.router)


def finished(env, request=None, *, status='stable'):
    value = submit(env, request or trend_request())
    result = {'series': [{'family': 'epm', 'operation': '', 'parameter': 'PARA1', 'status': status, 'delta': 0}], 'row_count': 2}
    with env.sessions.begin() as db:
        run = db.get(AnalysisRun, value['id'])
        run.status, run.result, run.completed_at = 'completed', result, utcnow()
    return value['id']


def save_body(env, identity, **extra):
    response = env.client.post(BASE + '/preview', json={'source_run_id': identity, 'interval_hours': 1,
        'workspace_token': database.workspace_token()})
    assert response.status_code == 200, response.text
    preview = response.json()
    return dict(source_run_id=identity, interval_hours=1, name='정기 PARA', workspace_token=database.workspace_token(),
        expected_run_revision=preview['source_run_revision'], first_run_at=preview['first_run_at'], **extra)


def make_schedule(env, request=None):
    identity = finished(env, request)
    body = save_body(env, identity)
    response = env.client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def act(env, schedule, action, **extra):
    return env.client.post(f"{BASE}/{schedule['id']}/actions", json=dict(action=action,
        expected_revision=schedule['revision'], workspace_token=database.workspace_token(), **extra))


def current(env, identity):
    return next(s for s in env.client.get(BASE).json()['items'] if s['id'] == identity)


def due(env, identity):
    at = utcnow() - timedelta(seconds=1)
    with env.sessions.begin() as db:
        db.get(AnalysisSchedule, identity).next_run_at = at
    return at


def test_preview_moves_all_windows_equally_preserving_duration_gap_and_offset(env):
    identity = finished(env, trend_request(baseline_start='2026-09-30T09:00:00+09:00', baseline_end='2026-10-02T09:00:00+09:00'))
    response = env.client.post(BASE + '/preview', json={'source_run_id': identity, 'workspace_token': database.workspace_token()})
    data = response.json()
    request = AnalysisRequest.model_validate(data['request'])
    assert request.current_end == datetime.fromisoformat(data['first_run_at'].replace('Z', '+00:00'))
    assert request.baseline_end - request.baseline_start == timedelta(days=2)
    assert request.current_start - request.baseline_end == timedelta(days=1)
    assert request.current_end - request.current_start == timedelta(days=2)
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisSchedule)) == 0


@pytest.mark.parametrize('kind', ['statistics', 'split'])
def test_rolling_other_kinds_keeps_scope_and_only_shifts_dates(kind):
    values = {'kind':kind, 'start':'2026-01-01T00:00:00Z','end':'2026-01-04T00:00:00Z'}
    if kind == 'split':
        values.update(operation='OP', recipe_a='A', recipe_b='B', retest_policy='all_events', min_samples=3, absolute_delta=1, lot_ids_a=['LOT1'])
    result = schedules.rolling_request(values, datetime(2026,2,4,tzinfo=timezone.utc))
    assert result.start == datetime(2026,2,1,tzinfo=timezone.utc)
    assert result.end == datetime(2026,2,4,tzinfo=timezone.utc)
    if kind == 'split':
        assert result.lot_ids_a == ['LOT1'] and result.recipe_a == 'A'


@pytest.mark.parametrize('change', [dict(status='queued'), dict(status='needs_input'), dict(status='failed'),
    dict(status='cancelled'), dict(request=trend_request(purpose='validation')),
    dict(request=trend_request(min_samples=None, absolute_delta=None)),dict(request={'kind':'statistics'}),
    dict(result={'series':[{'status':'needs_criteria'}]})])
def test_unconfirmed_unbounded_or_unjudged_runs_cannot_be_scheduled(env, change):
    identity = finished(env)
    with env.sessions.begin() as db:
        run = db.get(AnalysisRun, identity)
        for k,v in change.items():
            setattr(run,k,v)
    response = env.client.post(BASE + '/preview', json={'source_run_id':identity,'workspace_token':database.workspace_token()})
    assert response.status_code == 422, response.text


def test_save_is_idempotent_and_rechecks_time_revision_workspace(env):
    schedule, body = make_schedule(env)
    assert env.client.post(BASE, json=body).json()['id'] == schedule['id']
    assert env.client.post(BASE, json={**body,'name':'another'}).status_code == 409
    identity = finished(env)
    second = save_body(env, identity)
    for changes in ({'expected_run_revision':99}, {'workspace_token':'stale'}, {'first_run_at':utcnow().isoformat()}, {'first_run_at':(utcnow()+timedelta(days=3)).isoformat()}):
        assert env.client.post(BASE, json={**second,**changes}).status_code == 409
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisSchedule)) == 1


def test_due_slot_once_skip_missed_intervals_and_no_overlap(env):
    schedule, _ = make_schedule(env)
    at = due(env, schedule['id']) - timedelta(days=3)
    with env.sessions.begin() as db:
        db.get(AnalysisSchedule, schedule['id']).next_run_at = at
    schedules.scheduler_tick()
    schedules.scheduler_tick()
    with env.sessions() as db:
        saved = db.get(AnalysisSchedule, schedule['id'])
        run = db.get(AnalysisRun, saved.last_run_id)
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 2
        assert run.status == 'queued' and run.schedule_id == saved.id
        assert schedules.utc(run.scheduled_for) == at + timedelta(days=3)
        assert AnalysisRequest.model_validate(run.request).current_end == schedules.utc(run.scheduled_for)
    due(env, schedule['id'])
    schedules.scheduler_tick()
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 2


def test_normal_worker_change_notifications_dedupe_ack_and_recovery(env, monkeypatch):
    schedule, _ = make_schedule(env)
    def extract(snapshot, request):
        return [measurement(at=request.baseline_start.isoformat()), measurement(15, at=request.current_start.isoformat())], [], {}
    monkeypatch.setattr(service, 'extract', extract)
    for _ in range(2):
        due(env, schedule['id']);schedules.scheduler_tick()
        assert worker.tick()
        schedules.scheduler_tick()
    response = env.client.get(BASE).json()
    assert len(response['notifications']) == response['unread_count'] == 1
    notice = response['notifications'][0]
    assert notice['kind'] == 'changed'
    assert env.client.post(BASE + '/notifications/' + notice['id'] + '/ack', json={'workspace_token':'old'}).status_code == 409
    assert env.client.post(BASE + '/notifications/' + notice['id'] + '/ack', json={'workspace_token':database.workspace_token()}).status_code == 200
    assert env.client.get(BASE).json()['unread_count'] == 0
    monkeypatch.setattr(service, 'extract', lambda snapshot,request: ([measurement(at=request.baseline_start.isoformat()),measurement(at=request.current_start.isoformat())],[],{}))
    due(env, schedule['id']);schedules.scheduler_tick();worker.tick();schedules.scheduler_tick()
    response = env.client.get(BASE).json()
    assert len(response['notifications']) == 2 and response['unread_count'] == 1
    assert response['items'][0]['enabled']


@pytest.mark.parametrize('status', ['failed','cancelled','needs_input','needs_criteria'])
def test_terminal_problem_pauses_once_preserves_questions_and_resumption_guard(env,status):
    schedule,_ = make_schedule(env)
    due(env,schedule['id']);schedules.scheduler_tick()
    run_id = current(env,schedule['id'])['last_run_id']
    with env.sessions.begin() as db:
        run=db.get(AnalysisRun,run_id)
        run.status = 'completed' if status=='needs_criteria' else status
        if status=='needs_criteria':run.result={'series':[{'family':'epm','parameter':'NEW','status':'needs_criteria'}]}
        run.question_ids=['preserved']
    schedules.scheduler_tick();schedules.scheduler_tick()
    data=env.client.get(BASE).json()
    assert not data['items'][0]['enabled'] and len(data['notifications'])==1
    with env.sessions() as db:assert db.get(AnalysisRun,run_id).question_ids==['preserved']
    response=act(env,data['items'][0],'resume')
    assert response.status_code == (409 if status in {'needs_input','needs_criteria'} else 200), response.text
    if response.status_code==200:
        schedules.scheduler_tick()
        assert current(env,schedule['id'])['enabled']


def test_pause_only_stops_future_work_cancel_retry_stays_in_same_schedule(env,monkeypatch):
    schedule,_=make_schedule(env);due(env,schedule['id']);schedules.scheduler_tick()
    scheduled=current(env,schedule['id']);run_id=scheduled['last_run_id']
    assert act(env,scheduled,'pause').status_code==200
    with env.sessions() as db:assert db.get(AnalysisRun,run_id).status=='queued'
    def job(action, revision):
        return env.client.post('/api/workspace/activity/actions',json={'target':{'kind':'analysis_run','id':run_id},
            'action':action,'expected_revision':revision,'workspace_token':database.workspace_token()})
    assert job('cancel',1).status_code==200
    schedules.scheduler_tick()
    response=job('retry',2);assert response.status_code==200,response.text
    child=response.json()['target']['id']
    monkeypatch.setattr(service,'extract',lambda *args:([measurement()],[],{}))
    worker.tick();schedules.scheduler_tick()
    assert current(env,schedule['id'])['last_run_id']==child
    assert not current(env,schedule['id'])['enabled']
    with env.sessions() as db:
        first,second=db.get(AnalysisRun,run_id),db.get(AnalysisRun,child)
        assert first.status=='cancelled' and second.status=='completed'
        assert second.schedule_id==first.schedule_id and second.scheduled_for==first.scheduled_for
        assert second.request==first.request


def test_scope_source_owner_and_approved_reference_changes_pause_without_dispatch(env,monkeypatch):
    schedule,_=make_schedule(env)
    due(env,schedule['id'])
    with env.sessions.begin() as db:db.get(Source,'a-epm').columns=[]
    schedules.scheduler_tick()
    data=env.client.get(BASE).json()
    assert not data['items'][0]['enabled'] and data['notifications'][0]['kind']=='blocked'
    with env.sessions() as db:assert db.scalar(select(func.count()).select_from(AnalysisRun))==1


@pytest.mark.parametrize('failure',['owner','knowledge','project','method'])
def test_frozen_context_changes_block_automatic_execution(env,monkeypatch,failure):
    schedule,_=make_schedule(env);due(env,schedule['id'])
    with env.sessions.begin() as db:
        saved=db.get(AnalysisSchedule,schedule['id'])
        if failure=='owner':db.get(User,'admin').active=False
        elif failure=='knowledge':saved.request={**saved.request,'knowledge_refs':[{'item_id':'removed','version':1}]}
        elif failure=='project':saved.request={**saved.request,'_project_scope':{'id':99}}
        else:saved.method_version='outdated'
    schedules.scheduler_tick()
    with env.sessions() as db:
        assert not db.get(AnalysisSchedule,schedule['id']).enabled
        assert db.scalar(select(func.count()).select_from(AnalysisNotification))==1
        assert db.scalar(select(func.count()).select_from(AnalysisRun))==1


def test_queue_full_defers_without_fake_failure_or_new_questions(env):
    schedule,_=make_schedule(env)
    for _ in range(5):submit(env,trend_request())
    due(env,schedule['id']);schedules.scheduler_tick()
    item=current(env,schedule['id'])
    assert item['enabled'] and item['last_run_id'] is None
    assert env.client.get(BASE).json()['notifications']==[]


def test_new_confirmed_conditions_replace_paused_template_and_keep_old_history(env):
    schedule,_=make_schedule(env);due(env,schedule['id']);schedules.scheduler_tick()
    old=current(env,schedule['id'])['last_run_id']
    with env.sessions.begin() as db:db.get(AnalysisRun,old).status='needs_input'
    schedules.scheduler_tick()
    new=finished(env,trend_request(min_samples=3,absolute_delta=4))
    replacement=save_body(env,new)
    response=act(env,current(env,schedule['id']),'replace',replacement=replacement)
    assert response.status_code==200,response.text
    assert response.json()['enabled'] and response.json()['request']['absolute_delta']==4
    schedules.scheduler_tick()
    assert current(env,schedule['id'])['enabled']
    with env.sessions() as db:assert db.get(AnalysisRun,old).status=='needs_input'


def test_stale_settings_non_owner_and_other_database_cannot_mutate(env):
    schedule,_=make_schedule(env)
    assert act(env,schedule,'pause').status_code==200
    assert act(env,schedule,'resume').status_code==409
    with env.sessions.begin() as db:
        db.get(User,'admin').role='editor'
        db.get(AnalysisSchedule,schedule['id']).created_by='another'
    assert env.client.get(BASE).json()['items']==[]
    assert act(env,current_schedule:={**schedule,'revision':2},'resume').status_code==404
    with database.use_source(database.SourceBinding(None,'b')):
        assert env.client.get(BASE).json()['items']==[]
        assert act(env,current_schedule,'pause').status_code==404


def test_scheduled_new_series_question_approval_resume_and_update_existing_schedule(env,monkeypatch):
    from app.semantic import collector_config
    from app.semantic.collector_schemas import ExtractedKnowledge
    request=trend_request(family=None,parameter=None,min_samples=None,absolute_delta=None,
        series_criteria=[{'family':'epm','parameter':'PARA1','min_samples':1,'absolute_delta':2}])
    schedule,_=make_schedule(env,request)
    def extract(snapshot,request):
        return [measurement(at=request.baseline_start.isoformat()),measurement(20,at=request.current_start.isoformat()),
                measurement(12,at=request.baseline_start.isoformat(),parameter='NEW'),measurement(25,at=request.current_start.isoformat(),parameter='NEW')],[],{}
    monkeypatch.setattr(service,'extract',extract)
    due(env,schedule['id']);schedules.scheduler_tick();assert worker.tick();schedules.scheduler_tick()
    item=current(env,schedule['id']);assert not item['enabled']
    detail=env.client.get('/api/analytics/runs/'+item['last_run_id']).json()
    assert detail['status']=='completed' and len(detail['questions'])==1
    question=detail['questions'][0];qpath='/api/semantic/collection/questions/'+question['id']
    def llm(prompt,payload,schema):
        answer=payload['answers'][-1]
        return ExtractedKnowledge(name='새 PARA 기준',fields={'description':{'value':answer['text'],'quote':answer['text'],'answer_id':answer['id']}})
    monkeypatch.setattr(collector_config,'configured',lambda:True)
    monkeypatch.setattr(collector_config,'complete',llm)
    q=env.client.get(qpath).json()
    response=env.client.post(qpath+'/answers',json={'expected_revision':q['revision'],'text':'새 PARA도 최소 표본 1개, 변화량 2로 확인합니다.'})
    assert response.status_code==202,response.text
    draft=env.client.get(qpath).json()['draft']
    review=env.client.post('/api/semantic/collection/drafts/'+draft['id']+'/review',json={'expected_revision':draft['revision'],'decision':'approve','note':'정기 분석 기준 확인'})
    assert review.status_code==200,review.text
    approved=env.client.get('/api/analytics/runs/'+detail['id']).json()['questions'][0]['approved_definition']
    resumed=submit(env,trend_request(family=None,parameter=None,knowledge_refs=[{'item_id':approved['item_id'],'version':approved['version']}]))
    assert worker.tick()
    replacement=save_body(env,resumed['id'])
    response=act(env,current(env,schedule['id']),'replace',replacement=replacement)
    assert response.status_code==200,response.text
    due(env,schedule['id']);schedules.scheduler_tick();assert worker.tick();schedules.scheduler_tick()
    item=current(env,schedule['id']);assert item['enabled']
    result=env.client.get('/api/analytics/runs/'+item['last_run_id']).json()
    assert result['status']=='completed' and result['request']['knowledge_refs']==[{'item_id':approved['item_id'],'version':approved['version']}]
    assert env.client.get(qpath).json()['draft']['status']=='approved'


def test_processed_results_are_not_loaded_on_each_schedule_poll(env):
    from sqlalchemy import event
    schedule,_=make_schedule(env);due(env,schedule['id']);schedules.scheduler_tick()
    with env.sessions.begin() as db:
        saved=db.get(AnalysisSchedule,schedule['id']);run=db.get(AnalysisRun,saved.last_run_id)
        run.status,run.result='completed',{'series':[]}
        saved.processed_run_id=run.id
    reads=[]
    engine=env.sessions.kw['bind']
    def record(connection,cursor,statement,parameters,context,executemany):
        if statement.lstrip().startswith('SELECT'):reads.append(statement)
    event.listen(engine,'before_cursor_execute',record)
    try:schedules.scheduler_tick()
    finally:event.remove(engine,'before_cursor_execute',record)
    assert not any('app_analysis_runs.result' in s for s in reads)


def test_project_schedule_keeps_lot_scope_and_filters_then_stops_on_change(env,monkeypatch):
    from app.models import AppProject
    AppProject.__table__.create(env.sessions.kw['bind'])
    with env.sessions.begin() as db:
        db.add(AppProject(id=7,connection_key='a',name='Project',lot_cds=['XXXA']))
    schedule,_=make_schedule(env,trend_request(project_id=7))
    make_schedule(env)
    scoped=env.client.get(BASE+'?project_id=7').json()['items']
    assert [s['id'] for s in scoped]==[schedule['id']]
    seen=[]
    def extract(snapshot,request,project_lot_cds):
        seen.append(project_lot_cds)
        return [measurement(at=request.baseline_start.isoformat()),measurement(at=request.current_start.isoformat())],[],{}
    monkeypatch.setattr(service,'extract',extract)
    due(env,schedule['id']);schedules.scheduler_tick();worker.tick();schedules.scheduler_tick()
    assert seen==[['XXXA']]
    with env.sessions.begin() as db:db.get(AppProject,7).lot_cds=['YYYB']
    due(env,schedule['id']);schedules.scheduler_tick()
    assert not current(env,schedule['id'])['enabled']
    assert len(seen)==1


@pytest.mark.parametrize('when',['before','during'])
def test_scheduled_execution_rechecks_knowledge_before_read_and_before_result_commit(env,monkeypatch,when):
    from fastapi import HTTPException
    schedule,_=make_schedule(env);due(env,schedule['id']);schedules.scheduler_tick()
    current_id=current(env,schedule['id'])['last_run_id']
    blocked=when=='before'
    real=schedules.check_context
    def check(*args):
        if blocked:raise HTTPException(409,'승인 근거가 변경되었습니다.')
        return real(*args)
    def extract(*args):
        nonlocal blocked
        blocked=True
        return [measurement()],[],{'captured_at':utcnow().isoformat()}
    monkeypatch.setattr(schedules,'check_context',check)
    monkeypatch.setattr(service,'extract',extract)
    worker.tick();schedules.scheduler_tick()
    with env.sessions() as db:
        run=db.get(AnalysisRun,current_id)
        assert run.status=='failed' and run.result is None and not run.source_watermark
        assert not db.get(AnalysisSchedule,schedule['id']).enabled
