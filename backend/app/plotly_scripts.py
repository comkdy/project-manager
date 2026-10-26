"""Bounded subprocess transport; source credentials never enter a chart worker."""
import json
import os
from contextlib import ExitStack
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time

from fastapi import HTTPException

from .plotly_policy import (BATCH_ROWS, LOAD_TIMEOUT, MAX_CONTROL_BYTES, MAX_OUTPUT_BYTES,
                           MEMORY_BYTES, QUEUE_TIMEOUT, TIMEOUT, ScriptError, parse_script)

_slots = threading.BoundedSemaphore(2)
WORKER = Path(__file__).with_name('plotly_worker.py')


def _windows_job(process):
    """Kill-on-close + per-process memory limit; attach before stdin is released."""
    import ctypes as c
    from ctypes import wintypes as w
    class Basic(c.Structure):
        _fields_ = [('process_time',c.c_longlong),('job_time',c.c_longlong),('flags',w.DWORD),
                    ('min_working_set',c.c_size_t),('max_working_set',c.c_size_t),('active_processes',w.DWORD),
                    ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class IO(c.Structure):
        _fields_ = [(name,c.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(c.Structure):
        _fields_ = [('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),
                    ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
    kernel = c.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR]; kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]; kernel.SetInformationJobObject.restype=w.BOOL
    kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]; kernel.AssignProcessToJobObject.restype=w.BOOL
    kernel.CloseHandle.argtypes=[w.HANDLE]; kernel.CloseHandle.restype=w.BOOL
    handle = kernel.CreateJobObjectW(None,None)
    if not handle: raise OSError('Chart isolation unavailable')
    config = Extended()
    config.basic.flags = 0x2000 | 0x100 | 0x8  # KILL_ON_JOB_CLOSE, PROCESS_MEMORY, ACTIVE_PROCESS
    config.basic.active_processes = 1
    config.process_memory = MEMORY_BYTES
    if not kernel.SetInformationJobObject(handle,9,c.byref(config),c.sizeof(config)) or not kernel.AssignProcessToJobObject(handle,w.HANDLE(process._handle)):
        kernel.CloseHandle(handle)
        raise OSError('Chart isolation unavailable')
    return lambda:kernel.CloseHandle(handle)


def validate_script(script):
    try:
        parse_script(script)
    except ScriptError as error:
        raise HTTPException(422,str(error)) from None


@dataclass(frozen=True)
class ScriptResult:
    figure: dict
    input_rows: int
    input_bytes: int
    load_ms: float
    execution_ms: float
    worker_peak_bytes: int


def _spool_rows(folder, rows, columns):
    """Only a small batch is resident in the API; never materialize all rows/JSON."""
    started = time.monotonic()
    count, size = 0, 0
    names = [column['name'] for column in columns]
    iterator = iter(rows)
    try:
        with (Path(folder) / 'rows.jsonl').open('wb') as target:
            while batch := list(islice(iterator, BATCH_ROWS)):
                if time.monotonic() - started > LOAD_TIMEOUT:
                    raise HTTPException(408, '전체 데이터 전달 제한 시간을 초과했습니다. 일부 데이터로 실행하지 않았습니다.')
                values = [[row.get(name) if isinstance(row, dict) else row[index] for row in batch]
                          for index, name in enumerate(names)]
                encoded = json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
                if shutil.disk_usage(folder).free < len(encoded) + 64 * 1024 * 1024:
                    raise HTTPException(503, '차트 임시 저장 공간이 부족합니다. 일부 데이터로 실행하지 않았습니다.')
                target.write(encoded)
                count += len(batch)
                size += len(encoded)
        return count, size, round((time.monotonic() - started) * 1000, 2)
    finally:
        close = getattr(iterator, 'close', None)
        if close:
            close()  # Release a server-side cursor on all failures as well.


def execute_script(script, rows, columns, base_card=None):
    validate_script(script)
    # Acquire before iterating rows/opening the DB cursor or writing a spool file.
    if not _slots.acquire(timeout=QUEUE_TIMEOUT):
        raise HTTPException(429, '다른 카드의 스크립트를 처리 중입니다. 잠시 후 다시 실행하세요.')
    process, close_job = None, None
    def stop_worker():
        nonlocal close_job
        if close_job:
            close_job()
            close_job = None
        if process and process.poll() is None:
            process.kill()
            process.wait()
    try:
        with ExitStack() as stack:
            folder = stack.enter_context(tempfile.TemporaryDirectory(prefix='pm-chart-'))
            count, size, load_ms = _spool_rows(folder, rows, columns)
            payload = json.dumps({'script':script, 'columns':columns, 'base_card':base_card,
                                  'input_rows':count, 'protocol':'column_batches_v1'}, ensure_ascii=False).encode('utf-8')
            if len(payload) > MAX_CONTROL_BYTES:
                raise HTTPException(422, '스크립트와 컬럼 정의가 너무 큽니다.')
            env = {key:os.environ[key] for key in ('SYSTEMROOT','WINDIR','SystemRoot') if key in os.environ}
            env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
            # Avoid the Windows venv redirector: its child could start before
            # assignment to our Job Object and escape inherited resource limits.
            executable = getattr(sys, '_base_executable', sys.executable) if os.name=='nt' else sys.executable
            execution_started = time.monotonic()
            process = subprocess.Popen([executable,'-I','-B',str(WORKER),sysconfig.get_path('purelib')],cwd=folder,env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            # Stop/wait before deleting the data file, including unexpected
            # failures while the worker still has that file open on Windows.
            stack.callback(stop_worker)
            if os.name=='nt': close_job = _windows_job(process)
            try:
                output,_ = process.communicate(payload,timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                if close_job: close_job(); close_job = None
                process.kill(); process.communicate()
                raise HTTPException(422,f'스크립트가 {TIMEOUT}초 제한을 초과했습니다.') from None
            if process.returncode or not output:
                raise HTTPException(422,'스크립트 실행이 중단되었습니다. 메모리 또는 연산 크기를 줄이세요.')
            if len(output)>MAX_OUTPUT_BYTES:
                raise HTTPException(422, '차트 표시 결과가 너무 큽니다. df의 데이터는 생략하지 않았습니다. 스크립트에서 집계 후 표시하세요.')
            result = json.loads(output)
            if 'error' in result: raise HTTPException(422,result['error'])
            if result.get('input_rows') != count:
                raise HTTPException(503, '전체 데이터 전달을 확인하지 못했습니다. 일부 데이터로 실행하지 않았습니다.')
            return ScriptResult(result['figure'], count, size, load_ms,
                                round((time.monotonic() - execution_started) * 1000, 2),
                                result.get('peak_memory_bytes', 0))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(503,'격리된 차트 실행 환경을 사용할 수 없습니다.') from None
    finally:
        _slots.release()


def run_script(script, rows, columns, base_card=None):
    """Small/internal callers share exactly the same all-row transport."""
    return execute_script(script, rows, columns, base_card).figure
