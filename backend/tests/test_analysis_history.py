"""History uses existing source visibility, persisted evidence and job lineage."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from app import activity, db as database
from app.analytics import history, schedules, service, worker
from app.analytics.history_contract import ScheduleHistory, ScheduleComparison
from app.analytics.models import AnalysisNotification, AnalysisRun, AnalysisSchedule
from app.models import Source, User, utcnow
from test_analytics_service import env, measurement, trend_request
from test_analysis_schedules import make_schedule, due, current, finished, save_body, act

BASE = '/api/analytics/schedules'


@pytest.fixture(autouse=True)
def routes(env):
    for router in (schedules.router, history.router, activity.router):
        env.client.app.include_router(router)


def prepared(env):
    schedule, _ = make_schedule(env)
    with env.sessions.begin() as db:
        origin = db.get(AnalysisRun, schedule['origin_run_id'])
        origin.result = service.compute_request(service.AnalysisRequest.model_validate(origin.request),
            [measurement(at='2026-10-02T00:00:00Z'), measurement(at='2026-10-04T00:00:00Z')], [])
        origin.result = {**origin.result, 'method_version': service.METHOD_VERSION}
        db.get(AnalysisSchedule, schedule['id']).outcome = schedules.outcome(origin.result)
    return schedule


def execute(env, monkeypatch, schedule, value=15):
    monkeypatch.setattr(service, 'extract', lambda snapshot, request: ([
        measurement(at=request.baseline_start.isoformat()), measurement(value, at=request.current_start.isoformat())], [], {}))
    due(env, schedule['id']); schedules.scheduler_tick()
    assert worker.tick()
    schedules.scheduler_tick()
    return current(env, schedule['id'])['last_run_id']


def compare(env, schedule, run_id, **params):
    return env.client.get(f"{BASE}/{schedule['id']}/runs/{run_id}/comparison", params=params)


def test_exact_alert_pair_survives_another_run_and_template_replacement(env, monkeypatch):
    schedule = prepared(env)
    first = execute(env, monkeypatch, schedule)
    second = execute(env, monkeypatch, schedule, 16)
    response = compare(env, schedule, first)
    assert response.status_code == 200, response.text
    result = ScheduleComparison.model_validate(response.json())
    assert result.comparable and result.previous.id == schedule['origin_run_id']
    assert result.notification_change_count == result.changed_count == 1
    assert result.rows[0].previous.mean_b == 10 and result.rows[0].current.mean_b == 15
    assert result.rows[0].current.min_samples == 1 and result.rows[0].current.absolute_delta == 2
    assert result.rows[0].change == 'state'
    second_result = compare(env, schedule, second).json()
    assert second_result['previous']['id'] == first
    assert second_result['notification_change_count'] == 0 and second_result['rows'][0]['change'] == 'values'
    notices = env.client.get(BASE).json()['notifications']
    assert len(notices) == 1 and notices[0]['comparison_run_id'] == schedule['origin_run_id']
    replacement = finished(env, trend_request(absolute_delta=3))
    assert act(env, current(env, schedule['id']), 'replace', replacement=save_body(env, replacement)).status_code == 200
    after = compare(env, schedule, first).json()
    assert after['rows'] == response.json()['rows'] and after['previous']['id'] == schedule['origin_run_id']
    with env.sessions() as db:
        assert db.get(AnalysisSchedule, schedule['id']).outcome_run_id == replacement
    third = execute(env, monkeypatch, schedule)
    assert compare(env, schedule, third).json()['previous']['id'] == replacement


def test_cancel_retry_keeps_history_and_comparison_and_does_not_restart_schedule(env, monkeypatch):
    schedule = prepared(env)
    due(env, schedule['id']);schedules.scheduler_tick()
    identity = current(env, schedule['id'])['last_run_id']
    payload = {'target': {'kind':'analysis_run','id':identity}, 'workspace_token':database.workspace_token()}
    assert env.client.post('/api/workspace/activity/actions',json={**payload,'action':'cancel','expected_revision':1}).status_code == 200
    schedules.scheduler_tick()
    result = env.client.post('/api/workspace/activity/actions',json={**payload,'action':'retry','expected_revision':2})
    assert result.status_code == 200, result.text
    child = result.json()['target']['id']
    monkeypatch.setattr(service,'extract',lambda snapshot,request:([measurement(at=request.baseline_start.isoformat()),measurement(15,at=request.current_start.isoformat())],[],{}))
    assert worker.tick();schedules.scheduler_tick()
    listing = ScheduleHistory.model_validate(env.client.get(f"{BASE}/{schedule['id']}/history").json())
    rows = {r.id:r for r in listing.items}
    assert rows[identity].status == 'cancelled' and rows[identity].retry_run_id == child
    assert rows[child].retry_of == identity and rows[child].status == 'completed'
    assert rows[child].comparison_run_id == schedule['origin_run_id']
    assert rows[child].scheduled_for == rows[identity].scheduled_for
    assert not current(env,schedule['id'])['enabled']
    assert compare(env,schedule,child).json()['comparable']
    assert not compare(env,schedule,identity).json()['comparable']


def test_history_keyset_pages_equal_timestamps_without_loading_results(env):
    schedule = prepared(env)
    at = utcnow()
    with env.sessions.begin() as db:
        origin = db.get(AnalysisRun,schedule['origin_run_id'])
        for i in range(42):
            db.add(AnalysisRun(id=f'history-{i:03}',connection_key=origin.connection_key,kind=origin.kind,
                status='completed',request=origin.request,source_snapshot=origin.source_snapshot,
                result={'large':'result'},question_ids=[],created_by='admin',created_at=at,
                schedule_id=schedule['id'],comparison_run_id=origin.id))
    engine=env.sessions.kw['bind']; statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany): statements.append(statement.lower())
    event.listen(engine,'before_cursor_execute',capture)
    try:
        ids=[]; before=None
        while True:
            response=env.client.get(f"{BASE}/{schedule['id']}/history",params={'before':before} if before else {})
            assert response.status_code==200,response.text
            page=ScheduleHistory.model_validate(response.json())
            assert len(page.items)<=20
            ids.extend(r.id for r in page.items)
            before=page.next_cursor
            if not before:break
        assert len(ids)==len(set(ids))==43
        assert not any('app_analysis_runs.result' in s for s in statements)
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert env.client.get(f"{BASE}/{schedule['id']}/history",params={'before':'unknown'}).status_code==422


def test_history_and_evidence_obey_owner_role_workspace_and_source_visibility(env, monkeypatch):
    schedule = prepared(env);identity=execute(env,monkeypatch,schedule)
    with env.sessions.begin() as db:db.add(User(id='other',username='other',role='editor',name='Other'))
    env.actor.id,env.actor.role='other','editor'
    assert env.client.get(f"{BASE}/{schedule['id']}/history").status_code==404
    assert compare(env,schedule,identity).status_code==404
    env.actor.id,env.actor.role='reader','viewer'
    assert env.client.get(f"{BASE}/{schedule['id']}/history").status_code==403
    env.actor.id,env.actor.role='admin','admin'
    with env.sessions.begin() as db:db.get(Source,'a-epm').columns=[]
    data=env.client.get(f"{BASE}/{schedule['id']}/history").json()
    assert data['items']==[] and data['hidden_count']==2
    assert compare(env,schedule,identity).status_code==409
    monkeypatch.setattr(database,'active_source',database.SourceBinding(None,'b'))
    assert env.client.get(f"{BASE}/{schedule['id']}/history").status_code==404


def test_other_schedule_run_cannot_be_compared_under_an_owned_schedule(env,monkeypatch):
    schedule=prepared(env);other=prepared(env)
    identity=execute(env,monkeypatch,other)
    assert compare(env,schedule,identity).status_code==404


@pytest.mark.parametrize('change',['criteria','window','knowledge','method','missing_method','incomplete'])
def test_non_equivalent_evidence_never_labels_difference_as_data_change(env,monkeypatch,change):
    schedule=prepared(env);identity=execute(env,monkeypatch,schedule)
    with env.sessions.begin() as db:
        run=db.get(AnalysisRun,identity)
        if change=='criteria':run.request={**run.request,'absolute_delta':9}
        elif change=='window':
            request=schedules.public_request(run.request)
            run.request={**run.request,'baseline_start':(request.baseline_start-timedelta(hours=1)).isoformat()}
        elif change=='knowledge':run.request={**run.request,'knowledge_refs':[{'item_id':'approved','version':2}]}
        elif change=='method':run.result={**run.result,'method_version':'future'}
        elif change=='missing_method':run.result={k:v for k,v in run.result.items() if k!='method_version'}
        elif change=='incomplete':run.status='failed';run.result=None
    result=compare(env,schedule,identity).json()
    assert not result['comparable'] and result['reasons']
    assert result['notification_change_count'] is None and result['changed_count'] is None
    assert all(r['change']=='not_comparable' and r['notification_change'] is None for r in result['rows'])


def test_legacy_or_unavailable_baseline_does_not_guess_or_expose_it(env,monkeypatch):
    schedule=prepared(env);identity=execute(env,monkeypatch,schedule)
    with env.sessions.begin() as db:db.get(AnalysisRun,identity).comparison_run_id=None
    result=compare(env,schedule,identity).json()
    assert not result['comparable'] and result['previous'] is None
    with env.sessions.begin() as db:
        db.get(AnalysisRun,identity).comparison_run_id=schedule['origin_run_id']
        origin=db.get(AnalysisRun,schedule['origin_run_id'])
        origin.connection_key='b'
    result=compare(env,schedule,identity).json()
    assert not result['comparable'] and result['previous_request'] is None


def test_project_scope_change_reuses_result_access_guard(env,monkeypatch):
    schedule=prepared(env);identity=execute(env,monkeypatch,schedule)
    with env.sessions.begin() as db:
        run=db.get(AnalysisRun,identity)
        run.request={**run.request,'project_id':7,'_project_scope':{'id':7,'name':'P','lot_cds':['X']}}
    monkeypatch.setattr(service,'project_scope',lambda *args:{'id':7,'name':'P','lot_cds':['Y']})
    assert compare(env,schedule,identity).status_code==409
    listing=env.client.get(f"{BASE}/{schedule['id']}/history").json()
    assert identity not in [r['id'] for r in listing['items']] and listing['hidden_count']==1


def test_added_removed_direction_numeric_and_bounded_comparison_pages(env,monkeypatch):
    schedule=prepared(env);identity=execute(env,monkeypatch,schedule)
    with env.sessions.begin() as db:
        current_run=db.get(AnalysisRun,identity);old=db.get(AnalysisRun,schedule['origin_run_id'])
        series=deepcopy(current_run.result['series'][0])
        old_series=[{**series,'parameter':f'P{i:03}'} for i in range(205)]
        new_series=deepcopy(old_series)
        new_series[0]['delta']=-5
        new_series[1]['status']='insufficient_data'
        new_series[2]['parameter']='ADDED'
        new_series[3]['current']={**new_series[3]['current'],'mean':18}
        old.result={**old.result,'series':old_series};current_run.result={**current_run.result,'series':new_series}
    data=compare(env,schedule,identity).json()
    assert data['total_series']==206 and data['changed_count']==5 and data['notification_change_count']==4
    assert {r['change'] for r in data['rows']}=={'added','removed','direction','state','values'}
    full=compare(env,schedule,identity,differences_only=False).json()
    assert full['has_more'] and len(full['rows'])==100 and full['matched_count']==206
    tail=compare(env,schedule,identity,differences_only=False,offset=200).json()
    assert len(tail['rows'])==6 and not tail['has_more']
    assert compare(env,schedule,identity,offset=-1).status_code==422


@pytest.mark.parametrize('kind,field',[('statistics','statistics'),('split','group_a')])
def test_other_kinds_show_correct_evidence_and_statistics_has_no_change_alert(kind,field):
    from types import SimpleNamespace
    old=SimpleNamespace(kind=kind,result={'series':[{'family':'epm','parameter':'P',field:{'valid_count':4,'mean':10},'group_b':{'valid_count':3,'mean':20},'status':'stable'}]})
    new=deepcopy(old);new.result['series'][0][field]['mean']=12
    rows=history.compare_rows(old,new,True)
    assert rows[0].change=='values' and rows[0].previous.samples_a==4 and rows[0].current.mean_a==12
    assert not rows[0].notification_change
    assert rows[0].current.samples_b==(None if kind=='statistics' else 3)


def test_new_series_needing_criteria_uses_check_notice_instead_of_change_notice():
    from types import SimpleNamespace
    old=SimpleNamespace(kind='trend',result={'series':[{'family':'epm','parameter':'P','status':'stable'}]})
    new=SimpleNamespace(kind='trend',result={'series':[
        {'family':'epm','parameter':'P','status':'changed','delta':3},
        {'family':'epm','parameter':'NEW','status':'needs_criteria'}]})
    rows=history.compare_rows(old,new,True)
    assert {r.change for r in rows}=={'added','state'}
    assert not any(r.notification_change for r in rows)
