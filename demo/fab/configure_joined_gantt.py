"""Move the local demo cards to editable JOIN datasets without touching sources."""
from copy import deepcopy
import json
from time import perf_counter

from add_dashboard_examples import api, ROOT
from dense_schedule import MILESTONES

LINE_FILTERS = [dict(column='area_id', op='not_in', value=['VIRTUAL','CNC']),
                dict(column='samp_seq', op='eq', value=0),
                dict(column='main_oper_yn', op='eq', value='Y')]


def configure():
    assert api('/database-status')['database'] == 'wafer_fab_demo'
    snapshots = []
    for project in [None, *api('/admin/projects')]:
        if project and not project['active']: continue
        suffix = f"?project_id={project['id']}" if project else ''
        snapshots.append(dict(project=project, bootstrap=api('/bootstrap'+suffix), dashboard=api('/dashboards'+suffix)))
    backup = ROOT/'.local/gantt-joined-before.json'
    if not backup.exists(): backup.write_text(json.dumps(snapshots,ensure_ascii=False,indent=2),encoding='utf-8')
    report = []
    for snapshot in snapshots:
        project, bootstrap, board = (snapshot[k] for k in ('project','bootstrap','dashboard'))
        context = dict(project_id=project['id'] if project else None)
        cards = deepcopy(board['cards'])
        for card in cards:
            if card['kind'] != 'gantt' or card['gantt'].get('data_mode') == 'dataset': continue
            before = api('/cards/query', dict(card=card, context=context))
            template = api('/gantt/dataset-template', dict(card=card, context=context))
            ds = next((d for d in bootstrap['datasets'] if d['name']==template['name'] and d['project_id']==template['project_id']), None)
            if ds is None: ds = api('/datasets',template)
            card.update(dataset_id=ds['id'], dataset_version=ds['version'], gantt=dict(
                data_mode='dataset',as_of=card['gantt'].get('as_of'),line_filters=LINE_FILTERS,
                key_filters=[dict(column='operation',op='in',value=list(MILESTONES.values()))]))
            started=perf_counter()
            after = api('/cards/query',dict(card=card,context={**context,'refresh':True}))
            seconds=round(perf_counter()-started,3)
            fields=('lot_cd','lot_id','operation_seq','start_at','end_at','current_expected_end','expected_hours','tat_samples')
            assert [[r[k] for k in fields] for r in before['rows']]==[[r[k] for k in fields] for r in after['rows']], 'Schedule changed'
            assert [r['plan_end_at'] for r in before['meta']['lots']]==[r['plan_end_at'] for r in after['meta']['lots']]
            assert after['meta']['total_steps']==before['meta']['total_steps']
            report.append(dict(project=project['name'] if project else '전체 프로젝트',dataset=ds['id'],
                lots=after['meta']['lot_count'],steps=after['meta']['total_steps'],lines=sum(r['displayed'] for r in after['rows']),
                flags=sum(r['key_operation'] for r in after['rows']),seconds=seconds,schedule_unchanged=True))
        if cards != board['cards']:
            api('/dashboards/'+board['id'],dict(version=board['version'],cards=cards,layout=board['layout']),'PUT')
            persisted=api('/dashboards'+(f"?project_id={project['id']}" if project else ''))
            assert [c for c in persisted['cards'] if c['kind']!='gantt']==[c for c in board['cards'] if c['kind']!='gantt']
            assert persisted['layout']==board['layout']
    (ROOT/'.local/gantt-joined-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__': configure()
