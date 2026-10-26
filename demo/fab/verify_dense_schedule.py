"""Read-only invariants and real query comparison for the dense local fixture."""
from datetime import datetime
import json
import statistics
import sys
from types import SimpleNamespace
from time import perf_counter
from add_dashboard_examples import api, ROOT
from seed import connect, runtime, DATABASE
from configure_dense_gantt import DISPLAY_WHERE


def verify():
    manifest=json.loads((ROOT/'.local/fab-dense-manifest.json').read_text(encoding='utf-8'))
    assert api('/database-status')['database']==DATABASE
    checks={}
    with connect(runtime(),reader=True) as db:
        assert db.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        def check(name,sql):
            assert db.execute(sql).fetchone()[0]==0,name
            checks[name]=True
        check('1000_steps_500_main_50_skip',"""SELECT count(*) FROM (
          SELECT count(*) n,count(*) FILTER(WHERE main_oper_yn='Y') m,count(*) FILTER(WHERE skip_yn='Y') s
          FROM lot_plan GROUP BY lot_cd,lot_id) p WHERE n<>1000 OR m<>500 OR s<>50""")
        check('skipped_steps_have_no_history_or_tat',"""SELECT count(*) FROM lot_plan p
          LEFT JOIN lot_his h USING(lot_cd,lot_id,operation)
          LEFT JOIN operation_tat t USING(lot_cd,lot_id,operation)
          WHERE p.skip_yn='Y' AND (h.record_id IS NOT NULL OR t.tat IS NOT NULL)""")
        check('all_current_steps_are_non_skip',"""SELECT count(*) FROM current_status JOIN lot_plan USING(lot_cd,lot_id,operation) WHERE skip_yn='Y'""")
        check('completed_prefix_excludes_skip_only',"""SELECT count(*) FROM current_status c
          JOIN lot_plan cp USING(lot_cd,lot_id,operation)
          JOIN lot_plan p ON p.lot_cd=c.lot_cd AND p.lot_id=c.lot_id AND p.operation_seq<cp.operation_seq
          WHERE p.skip_yn!='Y' AND NOT EXISTS(SELECT 1 FROM lot_his h WHERE h.lot_cd=p.lot_cd AND h.lot_id=p.lot_id AND h.operation=p.operation)""")
        check('tat_is_elapsed_hours',"""WITH h AS (SELECT lot_cd,lot_id,operation,end_dt_tm,
          lag(end_dt_tm) OVER(PARTITION BY lot_cd,lot_id ORDER BY end_dt_tm) prev FROM lot_his)
          SELECT count(*) FROM h JOIN operation_tat t USING(lot_cd,lot_id,operation)
          WHERE prev IS NOT NULL AND abs(tat-extract(epoch FROM(end_dt_tm-prev))/3600)>.00001""")
        check('complete_lot_tat_is_30_to_60_days',"""SELECT count(*) FROM (
          SELECT t.lot_cd,t.lot_id,sum(tat)/24 days FROM operation_tat t
          WHERE NOT EXISTS(SELECT 1 FROM current_status c WHERE c.lot_cd=t.lot_cd AND c.lot_id=t.lot_id)
          GROUP BY t.lot_cd,t.lot_id) q WHERE days<30 OR days>60""")
        check('individual_tat_is_twenty_minutes_to_three_hours','SELECT count(*) FROM operation_tat WHERE tat<.334 OR tat>3')
        expected_display={row[0]:row[1] for row in db.execute('SELECT lot_id,count(*) FROM lot_plan WHERE '+DISPLAY_WHERE+' GROUP BY lot_id')}
        skipped={(row[0],row[1]) for row in db.execute("SELECT lot_id,operation FROM lot_plan WHERE skip_yn='Y'")}
        assert set(expected_display.values())=={manifest['displayed_steps_per_lot']}
        full_durations={}
        # Independent median reference for every eligible sample, then sum the
        # non-skipped future route, including hidden sub operations.
        for lot,cd,seq in db.execute('SELECT c.lot_id,c.lot_cd,p.operation_seq FROM current_status c JOIN lot_plan p USING(lot_cd,lot_id,operation)'):
            total=db.execute("""WITH med AS (SELECT p.lot_cd,p.operation,percentile_cont(.5) WITHIN GROUP(ORDER BY t.tat) hours
              FROM operation_tat t JOIN lot_plan p USING(lot_cd,lot_id,operation)
              WHERE p.skip_yn!='Y' GROUP BY p.lot_cd,p.operation)
              SELECT sum(m.hours) FROM lot_plan p JOIN med m USING(lot_cd,operation)
              WHERE p.lot_id=%s AND p.lot_cd=%s AND p.operation_seq>%s AND p.skip_yn!='Y'""",(lot,cd,seq)).fetchone()[0]
            full_durations[lot]=float(total or 0)
    boot=api('/bootstrap'); board=api('/dashboards')
    card=next(c for c in board['cards'] if c['kind']=='gantt')
    ds=next(d for d in boot['datasets'] if d['id']==card['dataset_id'])
    base=next(d for d in boot['datasets'] if d['name']=='lot_plan' and d['project_id'] is None)
    started=perf_counter(); overview=api('/cards/query',dict(card=card,context={'refresh':True})); overview_ms=(perf_counter()-started)*1000
    assert len(overview['rows'])==18*manifest['displayed_steps_per_lot'] and overview['meta']['total_steps']==18*950
    assert not any(r.get('is_summary') for r in overview['rows'])
    detail_times=[]
    for summary in overview['meta']['lots']:
        lot=summary['lot_id']; context={'gantt_detail_lot':lot}
        before=api('/cards/query',dict(card=dict(card,dataset_id=base['id'],dataset_version=base['version']),context=context))
        started=perf_counter(); after=api('/cards/query',dict(card=card,context=context));detail_times.append((perf_counter()-started)*1000)
        assert len(before['rows'])==950 and len(after['rows'])==expected_display[lot]
        lookup={r['operation']:r for r in before['rows']}
        for r in after['rows']:
            assert (lot,r['operation']) not in skipped
            assert all(r[k]==lookup[r['operation']][k] for k in ('start_at','end_at','current_expected_end','expected_hours'))
        future=[r for r in before['rows'] if r['status']=='upcoming']
        if future:
            first=datetime.fromisoformat(future[0]['start_at']);last=datetime.fromisoformat(future[-1]['end_at'])
            assert abs((last-first).total_seconds()/3600-full_durations[lot])<.00001
    checks.update(display_filter_preserves_all_timestamps=True,skip_never_in_timing_or_display=True,all_18_lots_verified=True)
    # Exercise an edited raw SQL that hides even the CURRENT operation, without
    # persisting a temporary dataset. Only the final planned operation is shown.
    sys.path.insert(0,str(ROOT/'backend'))
    from app.database_settings import load_active_source
    from app.db import use_source
    from app.query import chart_query
    from app.schemas import CardSpec,QueryContext
    edited=SimpleNamespace(spec=dict(ds['spec'],raw_sql='SELECT lot_cd,lot_id,operation_seq,operation FROM public.lot_plan WHERE operation_seq=1000'),
                           columns=[c for c in ds['columns'] if c['name'] in ('lot_cd','lot_id','operation_seq','operation')],project_id=None,version=ds['version'])
    lot=next(s['lot_id'] for s in overview['meta']['lots'] if s['current_operation']!='EPM')
    with use_source(load_active_source()):
        changed=chart_query(CardSpec.model_validate(card),QueryContext(gantt_detail_lot=lot),dataset=edited)
    assert len(changed['rows'])==1 and changed['meta']['total_steps']==950
    original=api('/cards/query',dict(card=card,context={'gantt_detail_lot':lot}))
    assert changed['rows'][0]['end_at']==next(r['end_at'] for r in original['rows'] if r['operation']=='EPM')
    checks['edited_sql_can_hide_current_without_compressing_time']=True
    report=dict(checks=checks,overview_rows=len(overview['rows']),overview_bytes=len(json.dumps(overview).encode()),
                overview_ms=round(overview_ms,2),detail_rows=manifest['displayed_steps_per_lot'],detail_median_ms=round(statistics.median(detail_times),2))
    (ROOT/'.local/fab-dense-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':verify()
