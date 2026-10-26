"""Cache hits still enforce fresh catalog, ownership and dataset definitions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from app import query as queries
from app.schemas import CardSpec, QueryContext
from test_card_latest_trellis import api, dataset, card


def test_encoded_api_and_internal_results_match_without_shared_mutation(api, dataset):
    value = card(dataset, kind='scatter', aggregate='none')
    original = queries.chart_query(CardSpec(**value), QueryContext(refresh=True))
    response = api.post('/api/cards/query', json={'card': value})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/json'
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['rows'] == original['rows']
    expected = response.json()
    original['rows'][0]['y'] = -999
    assert api.post('/api/cards/query', json={'card': value}).json() == expected


def test_hit_skips_recompiling_but_changed_catalog_cannot_reuse_it(api, dataset, monkeypatch):
    value = card(dataset, kind='kpi', x=None)
    assert api.post('/api/cards/query', json={'card': value}).status_code == 200
    compiled = []
    original_validate = queries.validate_card
    def validate(*args, **kwargs):
        compiled.append(1)
        return original_validate(*args, **kwargs)
    monkeypatch.setattr(queries, 'validate_card', validate)
    hit = api.post('/api/cards/query', json={'card': value})
    assert hit.json()['meta']['cached'] and not compiled
    original_load = queries.load_dataset
    def changed(*args, **kwargs):
        result = original_load(*args, **kwargs)
        if kwargs.get('with_catalog'):
            definition, catalog = result
            # Simulate a refreshed schema catalog between two requests without
            # touching the source DB, dataset version or cache invalidation.
            catalog.sources.pop(definition.spec['source_id'])
        return result
    monkeypatch.setattr(queries, 'load_dataset', changed)
    response = api.post('/api/cards/query', json={'card': value})
    assert response.status_code == 404
    assert compiled == [1]


def test_refresh_bypasses_completed_cache_and_draft_cannot_pollute_saved_card(api, dataset):
    value = CardSpec(**card(dataset, kind='kpi', x=None))
    queries.clear_cache()
    first = queries.chart_query(value, QueryContext())
    assert not first['meta']['cached']
    assert queries.chart_query(value, QueryContext())['meta']['cached']
    refreshed = queries.chart_query(value, QueryContext(refresh=True))
    assert not refreshed['meta']['cached']
    saved = queries.load_dataset(dataset['id'])
    saved.spec = {**saved.spec, 'raw_sql': 'SELECT id, equipment_id, project_id, value * 10 AS value FROM public.inline_measurements'}
    draft = queries.chart_query(value, QueryContext(), dataset=saved)
    assert not draft['meta']['cached']
    assert draft['rows'] != refreshed['rows']
    assert queries.chart_query(value, QueryContext())['rows'] == refreshed['rows']


def test_dashboard_cache_expires_after_one_minute_even_with_a_longer_setting(api, dataset, monkeypatch):
    now = [0]
    monkeypatch.setattr(queries.settings, 'cache_ttl_seconds', 600)
    monkeypatch.setattr(queries._chart_cache, 'clock', lambda: now[0])
    queries.clear_cache()
    value = card(dataset, kind='kpi', x=None)
    assert not api.post('/api/cards/query', json={'card': value}).json()['meta']['cached']
    now[0] = 59.99
    assert api.post('/api/cards/query', json={'card': value}).json()['meta']['cached']
    now[0] = 60
    assert not api.post('/api/cards/query', json={'card': value}).json()['meta']['cached']
    assert api.post('/api/cards/query', json={'card': value}).json()['meta']['cached']
    queries.clear_cache()


def test_thirty_api_viewers_execute_one_chart_query_per_refresh_cycle(api, dataset, monkeypatch):
    now = [0]
    monkeypatch.setattr(queries._chart_cache, 'clock', lambda: now[0])
    queries.clear_cache()
    value = card(dataset, kind='kpi', x=None)
    compute = queries._compute_chart
    lock, calls = Lock(), []
    def counted(*args, **kwargs):
        with lock:
            calls.append(1)
        return compute(*args, **kwargs)
    monkeypatch.setattr(queries, '_compute_chart', counted)
    for minute in range(3):
        now[0] = minute * 60
        gate = Barrier(30)
        def request():
            gate.wait(timeout=10)
            response = api.post('/api/cards/query', json={'card': value})
            assert response.status_code == 200, response.text
            return response.json()
        with ThreadPoolExecutor(max_workers=30) as pool:
            results = list(pool.map(lambda _: request(), range(30)))
        assert len(calls) == minute + 1
        assert sum(not result['meta']['cached'] for result in results) == 1
        assert len({result['meta']['queried_at'] for result in results}) == 1
    queries.clear_cache()


def test_python_card_five_and_thirty_viewers_share_full_query_and_worker(api, dataset, monkeypatch):
    now = [0]
    monkeypatch.setattr(queries._chart_cache, 'clock', lambda: now[0])
    queries.clear_cache()
    value = card(dataset, kind='kpi', x=None)
    value.update(editor_mode='python', script_input='dataset',
                 python_script='fig=go.Figure(go.Indicator(mode="number", value=len(df)))')
    original = queries._compute_script_card
    calls, lock = [], Lock()
    def counted(*args, **kwargs):
        with lock:
            calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(queries, '_compute_script_card', counted)
    for minute, clients in enumerate((5, 30)):
        now[0] = minute * 60
        gate = Barrier(clients)
        def request(_):
            gate.wait(timeout=10)
            response = api.post('/api/cards/query', json={'card': value})
            assert response.status_code == 200, response.text
            return response.json()
        with ThreadPoolExecutor(max_workers=clients) as pool:
            results = list(pool.map(request, range(clients)))
        assert len(calls) == minute + 1
        assert sum(not result['meta']['cached'] for result in results) == 1
        assert len({result['meta']['queried_at'] for result in results}) == 1
        assert all(result['rows'] == [] and result['meta']['input_rows'] == result['figure']['data'][0]['value']
                   for result in results)
    queries.clear_cache()
