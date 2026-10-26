"""Add bounded example charts to the named local fab fixture through app APIs."""
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from dense_schedule import MILESTONES

ROOT = Path(__file__).resolve().parents[2]


def api(path, body=None, method=None):
    request = Request('http://127.0.0.1:8000/api' + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      method=method, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'{error.code} {path}: {error.read().decode()}') from None


def main():
    assert api('/database-status')['database'] == 'wafer_fab_demo', 'Only the named local fixture is supported'
    scopes = [None, *api('/admin/projects')]
    scopes = [p for p in scopes if p is None or p['active']]
    backup = []
    for project in scopes:
        suffix = f"?project_id={project['id']}" if project else ''
        backup.append({'project': project, 'bootstrap': api('/bootstrap' + suffix),
                       'dashboard': api('/dashboards' + suffix)})
    path = ROOT / '.local/dashboard-examples-before.json'
    if not path.exists():
        path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding='utf-8')
    report = []
    for snapshot in backup:
        project, bootstrap, board = (snapshot[k] for k in ('project', 'bootstrap', 'dashboard'))
        pid = project['id'] if project else None
        sources = {s['table_name']: s['id'] for s in bootstrap['catalog']}
        # Shared custom definitions also appear in project catalogs. Reuse only
        # definitions owned by this exact scope, so project cards cannot widen it.
        datasets = {d['name']: d for d in bootstrap['datasets'] if d['project_id'] == pid}
        codes = project['lot_cds'] if project else []
        literals = ', '.join("'" + c.replace("'", "''") + "'" for c in codes)
        predicate = f"lot_cd = {literals}" if len(codes) == 1 else f"lot_cd IN ({literals})" if codes else 'TRUE'

        def dataset(name, spec):
            if name not in datasets:
                datasets[name] = api('/datasets', {'name': name, 'project_id': pid, 'spec': spec})
            return datasets[name]

        daily = dataset('예시 · Inline MAIN 일별 평균', {
            'source_id': sources['inline_data'],
            'raw_sql': f"""SELECT lot_cd, date_trunc('day', end_dt_tm AT TIME ZONE 'Asia/Seoul') AS measured_day,
       AVG(value) AS mean_value
FROM public.inline_data
WHERE {predicate} AND operation = 'oper A CD' AND parameter = 'MAIN'
GROUP BY lot_cd, date_trunc('day', end_dt_tm AT TIME ZONE 'Asia/Seoul')"""})
        duration = dataset('예시 · 완료 공정 소요 시간', {
            'source_id': sources['operation_tat'],
            'raw_sql': f"""SELECT t.lot_cd, t.lot_id, t.operation, t.tat, h.end_dt_tm
FROM public.operation_tat t
JOIN (SELECT lot_cd, lot_id, operation, MAX(end_dt_tm) AS end_dt_tm
      FROM public.lot_his GROUP BY lot_cd, lot_id, operation) h
  ON t.lot_cd = h.lot_cd AND t.lot_id = h.lot_id AND t.operation = h.operation
WHERE t.{predicate if codes else 'lot_cd IS NOT NULL'}"""})
        pivot_filters = [{'column': 'parameter', 'op': 'eq', 'value': 'MAIN'},
                         {'column': 'wf_id', 'op': 'eq', 'value': 1}]
        if codes:
            pivot_filters.append({'column': 'lot_cd', 'op': 'eq' if len(codes) == 1 else 'in',
                                  'value': codes[0] if len(codes) == 1 else codes})
        paired = dataset('예시 · Inline A-B 마지막 값 피벗', {
            'source_id': sources['inline_data'], 'pivot': {
                'keys': ['lot_cd', 'lot_id', 'wf_id', 'die_x', 'die_y'],
                'operation_column': 'operation', 'value_column': 'value',
                'time_column': 'end_dt_tm', 'tie_breaker': 'record_id',
                'outputs': [{'value': 'oper A CD', 'name': 'value_a'},
                            {'value': 'oper B CD', 'name': 'value_b'}],
                'require_all': True, 'filters': pivot_filters}})

        def card(key, title, ds, kind, x, y=None, aggregate='none', color=None, filters=None, period=True, **options):
            ident = 'fab-example-' + hashlib.sha256(f'{board["id"]}:{key}'.encode()).hexdigest()[:18]
            return {'id': ident, 'title': title, 'dataset_id': ds['id'], 'dataset_version': ds['version'],
                    'kind': kind, 'renderer': 'plotly', 'x': x, 'y': y, 'aggregate': aggregate,
                    'color': color, 'filters': filters or [], 'period': {'use_dashboard': period},
                    'options': {'palette': 'teal', **options}}

        examples = [
            card('inline-daily', 'Inline MAIN 일별 평균 · oper A CD', daily, 'line', 'measured_day', 'mean_value',
                 color='lot_cd', x_label='측정일', y_label='평균 측정값'),
            card('epm-distribution', 'EPM PARA1 측정값 분포', datasets['epm_data'], 'histogram', 'value',
                 filters=[{'column': 'parameter', 'op': 'eq', 'value': 'PARA1'}], bins=30,
                 x_label='PARA1 측정값', y_label='측정 건수'),
            card('completed-lots', '기간 내 대표 공정별 완료 랏 수', datasets['lot_his'], 'bar', 'operation',
                 aggregate='count', color='lot_cd', x_label='완료 공정', y_label='완료 랏 수',
                 filters=[{'column':'operation','op':'in','value':list(MILESTONES.values())}]),
            card('tat', '대표 공정별 평균 소요 시간', duration, 'bar', 'operation', 'tat', 'avg',
                 color='lot_cd', x_label='공정', y_label='평균 소요 시간 (hour)',
                 filters=[{'column':'operation','op':'in','value':list(MILESTONES.values())}]),
            card('paired', 'A/B 마지막 측정값 비교 · MAIN / wafer 1', paired, 'scatter', 'value_a', 'value_b',
                 color='lot_cd', x_label='oper A CD 마지막 값', y_label='oper B CD 마지막 값'),
        ]
        checks = []
        for item in examples:
            result = api('/cards/query', {'card': item, 'context': {
                'project_id': pid, 'period_start': {'year': 2026, 'week': 40}}})
            assert result['rows'], item['title']
            assert datasets[next(n for n, d in datasets.items() if d['id'] == item['dataset_id'])]['project_id'] == pid
            if codes and item.get('color') == 'lot_cd':
                assert {row['color'] for row in result['rows']} <= set(codes), item['title']
            assert not result['meta'].get('truncated') and not result['meta'].get('sampled'), item['title']
            if item['period']['use_dashboard']:
                assert result['meta']['period']['start'] == {'year': 2026, 'week': 40}
            checks.append({'title': item['title'], 'rows': len(result['rows']), 'duration_ms': result['meta'].get('duration_ms')})
        existing = {c['id'] for c in board['cards']}
        additions = [c for c in examples if c['id'] not in existing]
        if additions:
            positions = [(0, 0, 6, 8), (6, 0, 6, 8), (0, 8, 6, 8), (6, 8, 6, 8), (0, 16, 12, 9)]
            layout = [dict(i=c['id'], x=x, y=y, w=w, h=h)
                      for c, (x, y, w, h) in zip(examples, positions) if c['id'] not in existing]
            layout += [{**v, 'y': v['y'] + 25} for v in board['layout']]
            board = api('/dashboards/' + board['id'], {
                'version': board['version'], 'cards': additions + board['cards'], 'layout': layout}, 'PUT')
        else:
            replacements = {c['id']: c for c in examples}
            board = api('/dashboards/' + board['id'], {
                'version': board['version'],
                'cards': [replacements.get(c['id'], c) for c in board['cards']], 'layout': board['layout']}, 'PUT')
        assert all(c['id'] in {v['id'] for v in board['cards']} for c in examples)
        report.append({'project': project['name'] if project else '전체 프로젝트', 'added': len(additions),
                       'total_cards': len(board['cards']), 'checks': checks})
    (ROOT / '.local/dashboard-examples-verification.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
