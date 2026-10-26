from copy import deepcopy
from datetime import datetime, timezone
import pytest
from fastapi import HTTPException
from app.gantt import schedule_segments, display_segments
from app.schemas import GanttSettings

NOW = datetime(2026, 10, 22, 9, tzinfo=timezone.utc)


def route():
    return [dict(lot_cd='XXXA',lot_id='XXXA21',operation=op,operation_seq=i+1,current_operation='B',
                 current_started_at=None,completed_at='2026-10-22T08:00:00+00:00' if i==0 else None,
                 expected_hours=2,history_count=1 if i==0 else 0,tat_samples=10,tat_basis='same_lot_cd',remark=None)
            for i,op in enumerate(('A','B','C'))]


def test_hours_inference_and_future_never_turn_into_actual_history():
    rows=schedule_segments(route(),NOW)
    assert rows[0]['start_at'] is None and rows[0]['actual_completed_at'].hour==8
    assert rows[1]['estimated'] and rows[1]['end_at']==NOW
    assert rows[1]['current_expected_end'].hour==10
    assert rows[2]['start_at'].hour==10 and rows[2]['end_at'].hour==12
    assert rows[2]['actual_completed_at'] is None


def test_overrun_and_missing_tat_do_not_fabricate_future_dates():
    for expected in (.5,None):
        data=route();data[1]['expected_hours']=expected
        rows=schedule_segments(data,NOW)
        assert rows[1]['end_at']==NOW and rows[1]['status']=='current'
        assert rows[1]['current_expected_end'] is None
        assert rows[2]['start_at'] is None and rows[2]['end_at'] is None


def test_first_current_step_without_start_has_no_predictions():
    data=route()[1:];rows=schedule_segments(data,NOW)
    assert rows[0]['start_at'] is None and rows[1]['end_at'] is None
    data[0]['current_started_at']='2026-10-22T08:30:00Z'
    rows=schedule_segments(data,NOW)
    assert rows[0]['estimated'] is False
    assert rows[0]['current_expected_end'].hour==10 and rows[0]['current_expected_end'].minute==30


def test_rework_and_inconsistent_status_fail_explicitly():
    for data in (route()+[deepcopy(route()[0])],route()):
        if len(data)==3:data[2]['completed_at']='2026-10-22T08:30:00Z'
        with pytest.raises(HTTPException) as error:schedule_segments(data,NOW)
        assert error.value.status_code==422


def test_snapshot_requires_timezone_and_stays_json_serializable():
    from pydantic import ValidationError
    fields=dict(status_source_id='s',tat_source_id='t',history_source_id='h')
    with pytest.raises(ValidationError):GanttSettings(**fields,as_of='2026-10-22T09:00:00')
    assert isinstance(GanttSettings(**fields,as_of='2026-10-22T09:00:00Z').model_dump()['as_of'],str)


def test_hidden_current_and_sub_steps_preserve_future_times():
    data=route()
    data.append(dict(data[-1],operation='D',operation_seq=4))
    all_segments=schedule_segments(data,NOW)
    for r in data:r['displayed']=r['operation'] in ('A','D')
    visible, summaries=display_segments(schedule_segments(data,NOW))
    assert [r['operation'] for r in visible]==['A','D']
    assert visible[-1]['start_at']==all_segments[-1]['start_at']
    assert visible[-1]['end_at'].hour==14  # B and C still consume 2h each.
    assert summaries[0]['current_operation']=='B' and summaries[0]['total_steps']==4
    assert summaries[0]['displayed_steps']==2


def test_dense_display_keeps_each_operation_and_its_timestamps():
    data=route()[:2]+[dict(route()[-1],operation=f'step-{i}',operation_seq=i) for i in range(3,1001)]
    for r in data:r['displayed']=r['operation_seq']%2==0
    segments=schedule_segments(data,NOW)
    visible, summary=display_segments(segments)
    assert len(visible)==500
    assert [{k:v for k,v in r.items() if k!='interval_expected_hours'} for r in visible]==[r for r in segments if r['displayed']]
    assert visible[0]['interval_expected_hours']==4 and visible[-1]['interval_expected_hours']==2
    assert len({r['operation_seq'] for r in visible})==500
    assert not any('is_summary' in r for r in visible)
    assert summary[0]['total_steps']==1000 and summary[0]['displayed_steps']==500


def test_plan_end_includes_hidden_tail_without_altering_visible_operations():
    data=route()+[dict(route()[-1],operation='hidden-tail',operation_seq=4,expected_hours=3)]
    for r in data:r['displayed']=r['operation']=='B'
    segments=schedule_segments(data,NOW)
    visible, summary=display_segments(segments)
    assert len(visible)==1 and visible[0]['current_expected_end'].hour==10
    assert summary[0]['plan_end_at'].hour==15  # C 2h + hidden tail 3h.
    assert {k:v for k,v in visible[0].items() if k!='interval_expected_hours'}==segments[1]
    assert visible[0]['interval_expected_hours']==7  # B + C + hidden tail.


def test_unknown_hidden_tail_and_overrunning_last_step_have_no_plan_end():
    data=route();data[-1]['displayed']=False;data[-1]['expected_hours']=None
    assert display_segments(schedule_segments(data,NOW))[1][0]['plan_end_at'] is None
    data=route()[:2];data[-1]['expected_hours']=.5
    assert display_segments(schedule_segments(data,NOW))[1][0]['plan_end_at'] is None
    data[-1]['expected_hours']=2
    assert display_segments(schedule_segments(data,NOW))[1][0]['plan_end_at'].hour==10


def test_milestone_expected_tat_uses_samples_not_elapsed_time_and_propagates_unknowns():
    data=route()+[dict(route()[-1],operation='D',operation_seq=4,expected_hours=3)]
    for row in data:row['displayed']=row['operation'] in ('A','C')
    segments=schedule_segments(data,NOW)
    visible, _=display_segments(segments)
    assert [r['interval_expected_hours'] for r in visible]==[4,5]
    assert visible[1]['start_at'].hour==10
    assert all('interval_expected_hours' not in r for r in segments)
    data[-1]['expected_hours']=None
    visible, _=display_segments(schedule_segments(data,NOW))
    assert visible[0]['interval_expected_hours']==4
    assert visible[1]['interval_expected_hours'] is None


def test_independent_marker_filters_never_change_ribbon_or_hidden_tail():
    data = route() + [dict(route()[-1], operation='tail', operation_seq=4, expected_hours=3)]
    all_rows = schedule_segments(data, NOW)
    baseline = display_segments(all_rows, presentation_filters=True)[1][0]
    for row in data:
        row['displayed'] = row['operation'] == 'A'
        row['key_operation'] = row['operation'] == 'C'
    shown, summaries = display_segments(schedule_segments(data, NOW), presentation_filters=True)
    assert [r['operation'] for r in shown] == ['A', 'C']
    assert shown[0]['displayed'] and not shown[0]['key_operation']
    assert shown[1]['key_operation'] and not shown[1]['displayed']
    assert shown[1]['expected_hours'] == 2  # Own TAT, not the hidden tail's 3h.
    assert summaries[0]['ribbon'] == baseline['ribbon']
    assert summaries[0]['plan_end_at'] == baseline['plan_end_at']
    for row in data: row.update(displayed=False, key_operation=False)
    shown, summaries = display_segments(schedule_segments(data, NOW), presentation_filters=True)
    assert len(shown) == 1 and not shown[0]['displayed'] and not shown[0]['key_operation']
    assert summaries[0]['line_steps'] == summaries[0]['key_steps'] == 0
    assert summaries[0]['ribbon'] == baseline['ribbon']


def test_join_template_exposes_all_sources_and_preserves_sql_scope_literals():
    from types import SimpleNamespace
    from app.gantt_dataset import schedule_sql
    from app.direct_sql import parse_query, referenced_sources
    sources = {role: SimpleNamespace(id=role, schema_name='public', table_name=table,
               columns=[{'name': name} for name in ('lot_cd','lot_id','operation_seq','operation','area_id','samp_seq','main_oper_yn','skip_yn')])
               for role, table in [('plan','lot_plan'),('status','current_status'),('history','lot_his'),('tat','operation_tat')]}
    code = "X'); DELETE FROM lot_plan; --"
    sql = schedule_sql(sources, lot_cds=[code])
    refs = referenced_sources(parse_query(sql), [vars(s) for s in sources.values()])
    assert {r['id'] for r in refs} == set(sources)
    assert "skip_yn <> 'Y'" in sql and 'PERCENTILE_CONT' in sql
    assert 'area_id NOT IN' not in sql  # Line selection belongs to card presentation.
    from sqlglot import exp
    assert code in [n.this for n in parse_query(sql).find_all(exp.Literal) if n.is_string]


def test_joined_mode_validates_contract_and_display_filters_without_physical_tables():
    from app.gantt import JOINED_COLUMNS, validate_gantt
    from app.schemas import CardSpec
    numeric = {'operation_seq','expected_hours','history_count','tat_samples'}
    columns = [{'name':n,'type':'numeric' if n in numeric else 'timestamp with time zone' if n in ('current_started_at','completed_at') else 'text'} for n in JOINED_COLUMNS]
    card = CardSpec(id='test',title='test',dataset_id='test',dataset_version=1,kind='gantt',gantt={'data_mode':'dataset'})
    compiled = {'columns': columns, 'source': {'id':'any_joined_source'}}
    assert validate_gantt(card, compiled) is compiled
    with pytest.raises(HTTPException) as error:
        validate_gantt(card, dict(compiled, columns=[c for c in columns if c['name']!='expected_hours']))
    assert 'expected_hours' in error.value.detail
    card.gantt.key_filters = [{'column':'unknown','op':'eq','value':1}]
    with pytest.raises(HTTPException) as error: validate_gantt(card, compiled)
    assert 'unknown' in error.value.detail


def test_dataset_execution_embeds_authored_query_and_binds_marker_values(monkeypatch):
    from app import query
    from app.gantt import compute_dataset_gantt, JOINED_COLUMNS
    from app.schemas import CardSpec, QueryContext
    sql = 'SELECT custom_columns FROM public.custom_plan JOIN public.custom_status USING (lot_id)'
    attack = "A'); DELETE FROM public.custom_plan; --"
    card = CardSpec(id='c',title='t',dataset_id='d',dataset_version=1,kind='gantt',
                   gantt={'data_mode':'dataset','as_of':NOW.isoformat(),
                          'line_filters':[{'column':'operation','op':'not_in','value':[attack]}],
                          'key_filters':[{'column':'operation','op':'eq','value':'C'}]})
    captured = {}
    def execute(statement, params):
        captured.update(sql=statement, params=params)
        return [dict(r,__gantt_line=r['operation']!='B',__gantt_key=r['operation']=='B',__gantt_more=False) for r in route()]
    monkeypatch.setattr(query, 'execute_rows', execute)
    result = compute_dataset_gantt(card, QueryContext(), {'direct_sql':sql,'columns':[{'name':n,'type':'text'} for n in JOINED_COLUMNS]})
    assert sql in captured['sql']
    assert attack not in captured['sql'] and attack in captured['params'].values()
    assert 'public.lot_plan' not in captured['sql']  # No implicit source joins.
    assert result['meta']['total_steps']==3 and result['meta']['lots'][0]['key_steps']==1
