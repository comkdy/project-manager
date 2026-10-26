import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from app.chart_cache import ChartCache, ChartPayload


def payload(value=1):
    return ChartPayload.from_result({'rows': [{'x': '측정', 'y': value}], 'meta': {'cached': False}})


def test_script_figure_survives_fresh_and_cached_responses():
    figure = {'data': [{'type': 'pie', 'values': [1, 2]}], 'layout': {'title': '구성'}}
    response = ChartPayload.from_result({'rows': [], 'meta': {}, 'figure': figure})
    figure['data'][0]['values'][0] = 999
    for encoded in (response.fresh, response.cached):
        result = json.loads(encoded)
        assert result['figure']['data'][0]['values'] == [1, 2]
        assert result['figure']['layout']['title'] == '구성'


def test_concurrent_requests_compute_once_and_return_immutable_responses():
    cache = ChartCache(lambda: 60)
    gate = Barrier(12)
    calls = []
    def compute():
        calls.append(1)
        return payload()
    def request():
        gate.wait(timeout=5)
        return cache.get_or_compute('same', compute)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: request(), range(12)))
    assert len(calls) == 1
    assert sum(not hit for _, hit in results) == 1
    assert all(item is results[0][0] for item, _ in results)
    decoded = json.loads(results[0][0].cached)
    decoded['rows'][0]['y'] = 999
    assert json.loads(results[0][0].fresh)['rows'][0]['y'] == 1
    assert json.loads(results[0][0].cached)['meta']['cached'] is True
    assert json.loads(results[0][0].fresh)['meta']['cached'] is False


def test_invalidation_does_not_join_or_repopulate_old_inflight_result():
    cache = ChartCache(lambda: 60)
    entered, release = Event(), Event()
    def old():
        entered.set()
        assert release.wait(5)
        return payload(1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(cache.get_or_compute, 'key', old)
        assert entered.wait(5)
        cache.clear()
        newer, hit = cache.get_or_compute('key', lambda: payload(2))
        release.set()
        older, _ = request.result(timeout=5)
    assert not hit and newer != older
    current, hit = cache.get_or_compute('key', lambda: pytest.fail('recomputed'))
    assert hit and current == newer


def test_failed_computation_releases_waiters_and_allows_retry(monkeypatch):
    from app import chart_cache
    entered, waiting, release = Event(), Event(), Event()
    original_result = chart_cache.Future.result
    def observed_result(self, *args, **kwargs):
        waiting.set()
        return original_result(self, *args, **kwargs)
    monkeypatch.setattr(chart_cache.Future, 'result', observed_result)
    cache = ChartCache(lambda: 60)
    def fail():
        entered.set()
        assert release.wait(5)
        raise ValueError('source unavailable')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_compute, 'key', fail)
        assert entered.wait(5)
        second = pool.submit(cache.get_or_compute, 'key', fail)
        assert waiting.wait(5)
        release.set()
        for result in [first, second]:
            with pytest.raises(ValueError, match='source unavailable'):
                result.result(timeout=5)
    assert cache.get_or_compute('key', lambda: payload())[1] is False


def test_expiry_refresh_and_workspace_keys():
    now = [0]
    cache = ChartCache(lambda: 60, clock=lambda: now[0])
    first, _ = cache.get_or_compute('db-a:1', lambda: payload(1))
    assert cache.get_or_compute('db-a:1', lambda: payload(2))[0] == first
    assert cache.get_or_compute('db-b:1', lambda: payload(2))[1] is False
    assert cache.get_or_compute('db-a:2', lambda: payload(2))[1] is False
    refreshed, hit = cache.get_or_compute('db-a:1', lambda: payload(3), refresh=True)
    assert not hit and refreshed != first
    now[0] = 60
    assert cache.get_or_compute('db-a:1', lambda: payload(4))[1] is False


def test_memory_and_entry_limits_include_both_encoded_responses():
    item = payload()
    cache = ChartCache(lambda: 60, max_bytes=item.size * 2, max_entries=2)
    for key in ['a', 'b', 'c']:
        cache.get_or_compute(key, lambda: item)
    assert cache.get_or_compute('b', lambda: pytest.fail('evicted too early'))[1]
    assert not cache.get_or_compute('a', lambda: item)[1]
    tiny = ChartCache(lambda: 60, max_bytes=item.size - 1)
    assert not tiny.get_or_compute('large', lambda: item)[1]
    assert not tiny.get_or_compute('large', lambda: item)[1]
    cache.get_or_compute('changed', lambda: item)
    cache.get_or_compute('changed', lambda: payload('x' * 1000), refresh=True)
    assert not cache.get_or_compute('changed', lambda: item)[1]


def test_thirty_viewers_share_each_minute_refresh_and_staggered_cache_hits():
    now = [0]
    cache = ChartCache(lambda: 60, clock=lambda: now[0])
    calls = []
    def compute():
        calls.append(now[0])
        return payload(len(calls))
    for minute in range(3):
        now[0] = minute * 60
        gate = Barrier(30)
        def request():
            gate.wait(timeout=5)
            return cache.get_or_compute('same-query-and-period', compute)
        with ThreadPoolExecutor(max_workers=30) as pool:
            results = list(pool.map(lambda _: request(), range(30)))
        assert len(calls) == minute + 1
        assert sum(not hit for _, hit in results) == 1
        assert all(json.loads(item.fresh)['rows'][0]['y'] == minute + 1 for item, _ in results)
        # Users whose timers are not synchronized still reuse that minute's result.
        for offset in (0.1, 12, 40, 59.99):
            now[0] = minute * 60 + offset
            assert cache.get_or_compute('same-query-and-period', compute)[1]
        assert len(calls) == minute + 1
