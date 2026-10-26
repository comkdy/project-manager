"""Connect existing local demo Gantt cards to editable display datasets."""
import json
from add_dashboard_examples import api, ROOT

DISPLAY_WHERE = """skip_yn != 'Y'
  AND area_id NOT IN ('VIRTUAL', 'CNC')
  AND samp_seq = 0
  AND main_oper_yn = 'Y'"""


def configure():
    assert api('/database-status')['database']=='wafer_fab_demo'
    snapshots=[]
    for project in [None,*api('/admin/projects')]:
        if project and not project['active']: continue
        suffix=f"?project_id={project['id']}" if project else ''
        snapshots.append(dict(project=project,bootstrap=api('/bootstrap'+suffix),dashboard=api('/dashboards'+suffix)))
    backup=ROOT/'.local/gantt-display-before.json'
    if not backup.exists():backup.write_text(json.dumps(snapshots,ensure_ascii=False,indent=2),encoding='utf-8')
    report=[]
    for snapshot in snapshots:
        project,bootstrap,board=(snapshot[k] for k in ('project','bootstrap','dashboard'))
        pid=project['id'] if project else None
        source=next(s for s in bootstrap['catalog'] if s['schema_name']=='public' and s['table_name']=='lot_plan')
        assert {'area_id','samp_seq','main_oper_yn','skip_yn'}<={c['name'] for c in source['columns']}
        predicate=DISPLAY_WHERE
        if project:
            codes=project['lot_cds'];literals=', '.join("'"+c.replace("'","''")+"'" for c in codes)
            predicate+=f"\n  AND lot_cd = {literals}" if len(codes)==1 else f"\n  AND lot_cd IN ({literals})"
        spec=dict(source_id=source['id'],raw_sql=f"""SELECT lot_cd, lot_id, operation_seq, operation,
       area_id, samp_seq, main_oper_yn, skip_yn
FROM public.lot_plan
WHERE {predicate}""")
        name='간트 · 표시 공정'
        ds=next((d for d in bootstrap['datasets'] if d['name']==name and d['project_id']==pid),None)
        if ds is None:ds=api('/datasets',dict(name=name,project_id=pid,spec=spec))
        # Reruns preserve user edits to the display dataset.
        cards=[dict(c,dataset_id=ds['id'],dataset_version=ds['version']) if c['kind']=='gantt' else c for c in board['cards']]
        assert any(c['kind']=='gantt' for c in cards)
        checks=[]
        for card in cards:
            if card['kind']!='gantt':continue
            result=api('/cards/query',dict(card=card,context=dict(project_id=pid)))
            assert result['rows'] and not any(r.get('is_summary') for r in result['rows'])
            checks.append(dict(lots=result['meta']['lot_count'],calculated_steps=result['meta']['total_steps'],returned_rows=len(result['rows'])))
        api('/dashboards/'+board['id'],dict(version=board['version'],cards=cards,layout=board['layout']),'PUT')
        report.append(dict(project=project['name'] if project else '전체 프로젝트',dataset_id=ds['id'],checks=checks))
    (ROOT/'.local/gantt-display-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':configure()
