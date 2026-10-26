"""Read-only large-df benchmark; no tables, datasets, or cards are created.

Run with the local demo's existing read-only connection. PostgreSQL generates
rows on demand; the production cursor, spool, worker and response cache run.
HTTP authentication/coalescing is covered by test_chart_cache_api.py separately.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Barrier, Lock
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/tests'))
sys.path.insert(0, str(ROOT / 'backend'))
from conftest import configure_local_fixture_source
configure_local_fixture_source()

from app.db import settings
from app.chart_cache import ChartCache, ChartPayload
from app.plotly_codegen import dataset_script
from app import plotly_scripts as runner
from app.query import _compute_script_card
from app.schemas import CardSpec, QueryContext


def parent_peak_bytes():
    if sys.platform != 'win32':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    import ctypes as c
    from ctypes import wintypes as w
    class Counters(c.Structure):
        _fields_ = [('cb', w.DWORD), ('faults', w.DWORD),
                    *[(name, c.c_size_t) for name in ('peak', 'working', 'a', 'b', 'c', 'd', 'e', 'f')]]
    kernel, psapi = c.WinDLL('kernel32'), c.WinDLL('psapi')
    kernel.GetCurrentProcess.restype = w.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(Counters), w.DWORD]
    stats = Counters()
    stats.cb = c.sizeof(stats)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), c.byref(stats), stats.cb):
        raise RuntimeError('Memory measurement unavailable')
    return stats.peak


def benchmark(total, clients):
    names = ['lot_id', 'wf_id', 'die_x', 'die_y', 'operation', 'parameter', 'value', 'end_dt_tm', 'lot_cd']
    types = ['text', 'integer', 'integer', 'integer', 'text', 'text', 'double precision', 'timestamp', 'text']
    columns = [dict(name=name, type=kind) for name, kind in zip(names, types)]
    sql = f"""SELECT 'XXXA' || (1+(n-1)/10000)::text AS lot_id,
      (1+(n-1)%15)::integer AS wf_id, (n%21)::integer AS die_x, ((n/21)%21)::integer AS die_y,
      'oper ' || (n%20)::text AS operation, 'PARA1'::text AS parameter,
      (n%1000)::double precision/100 AS value,
      timestamp '2026-10-22' + (n%1000) * interval '1 minute' AS end_dt_tm, 'XXXA'::text AS lot_cd
      FROM generate_series(1,{int(total)}) AS n"""
    base = dict(id='load', title='All rows histogram', dataset_id='synthetic-readonly',
                dataset_version=1, kind='histogram', x='value', aggregate='none',
                editor_mode='python', script_input='dataset', period={'use_dashboard':False},
                options={'bins':30})
    card = CardSpec.model_validate({**base, 'python_script':dataset_script(base, columns)})
    compiled = {'direct_sql':sql, 'columns':columns}
    gate, lock, executions = Barrier(clients), Lock(), []
    cache = ChartCache(lambda:60)
    original = runner.execute_script
    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        with lock:
            executions.append(value)
        return value
    runner.execute_script = capture
    def request(_):
        gate.wait(timeout=10)
        started = time.monotonic()
        payload, cached = cache.get_or_compute('same-query', lambda:ChartPayload.from_result(
            _compute_script_card(card, QueryContext(), compiled)))
        return time.monotonic()-started, payload, cached
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=clients) as pool:
            responses = list(pool.map(request, range(clients)))
    finally:
        runner.execute_script = original
    value = json.loads(responses[0][1].fresh)
    assert len(executions) == 1
    assert sum(not cached for _, _, cached in responses) == 1
    assert value['rows'] == [] and value['meta']['input_rows'] == total
    assert sum(y for trace in value['figure']['data'] for y in trace['y']) == total
    execution = executions[0]
    durations = sorted(duration*1000 for duration, _, _ in responses)
    return dict(input_rows=total, clients=clients, database_and_worker_executions=len(executions),
                histogram_count=total, elapsed_seconds=round(time.monotonic()-started,3),
                p50_ms=round(durations[len(durations)//2],2), p95_ms=round(durations[int((len(durations)-1)*.95)],2),
                load_ms=execution.load_ms, execution_ms=execution.execution_ms,
                input_mib=round(execution.input_bytes/1024**2,2), response_bytes=len(responses[0][1].fresh),
                worker_peak_mib=round(execution.worker_peak_bytes/1024**2,2),
                api_process_peak_mib=round(parent_peak_bytes()/1024**2,2))


if __name__ == '__main__':
    if not settings.demo_mode or not settings.source_database_url:
        raise SystemExit('Requires the explicitly configured local demo source')
    results = []
    for rows, clients in ((100000,5),(1000000,30)):
        result = benchmark(rows, clients)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    (ROOT / '.local/python-script-load-test.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
