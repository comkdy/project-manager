"""Measure real PostgreSQL-backed API paths without result caching.

Run after seeding a named demo profile. No source data is changed.
"""
import argparse
import ctypes
import json
import statistics
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import settings, source_engine
from app.main import app


class ProcessMemory(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def memory():
    try:
        details = ProcessMemory()
        details.cb = ctypes.sizeof(details)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemory), ctypes.c_ulong]
        if ctypes.windll.psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(details), details.cb):
            return {"working_set_mb": round(details.WorkingSetSize / 1048576, 2),
                    "peak_working_set_mb": round(details.PeakWorkingSetSize / 1048576, 2)}
    except (AttributeError, OSError):
        pass
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["dev", "perf"], required=True)
    args = parser.parse_args()
    if not settings.demo_mode or settings.auth_mode != "local_admin":
        raise SystemExit("Benchmark requires the isolated DEMO_MODE database and AUTH_MODE=local_admin")
    with source_engine.connect() as connection:
        counts = {name: connection.execute(text(f'SELECT count(*) FROM public."{name}"')).scalar()
                  for name in ["wafers", "inline_measurements", "wat_measurements", "die_results"]}
    report = {"profile": args.profile, "row_counts": counts, "cache": "application cache bypassed; database cache warm after first pass", "cases": []}
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        session = client.get("/api/auth/session")
        session.raise_for_status()
        if session.json()["status"] != "authenticated":
            raise RuntimeError("Local test administrator is unavailable")
        bootstrap = client.get("/api/bootstrap").json()
        board = client.get("/api/dashboards").json()
        for card in board["cards"]:
            times = []
            for _ in range(3):
                started = time.perf_counter()
                response = client.post("/api/cards/query", json={"card": card, "context": {"refresh": True}})
                times.append(round((time.perf_counter() - started) * 1000, 2))
                response.raise_for_status()
            result = response.json()
            report["cases"].append({"name": card["title"], "kind": card["kind"], "milliseconds": times,
                                    "median_ms": statistics.median(times), "returned_rows": len(result["rows"]),
                                    "response_bytes": len(response.content), "mode": result["meta"]["mode"],
                                    "cached": result["meta"]["cached"], **memory()})
        source = next(d for d in bootstrap["datasets"] if d["spec"]["source_id"] == "inline_measurements")
        body = {"dataset_id": source["id"], "dataset_version": source["version"], "context": {"limit": 200}}
        for page in range(3):
            started = time.perf_counter()
            response = client.post("/api/query", json=body)
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            response.raise_for_status()
            result = response.json()
            report["cases"].append({"name": f"inline cursor page {page+1}", "median_ms": elapsed,
                                    "returned_rows": len(result["rows"]), "response_bytes": len(response.content), **memory()})
            body["context"]["cursor"] = result["next_cursor"]
    path = Path(__file__).resolve().parents[1] / ".local" / f"api-benchmark-{args.profile}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "row_counts": counts,
                      "timings": [{"name": c["name"], "median_ms": c["median_ms"]} for c in report["cases"]]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
