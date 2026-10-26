"""Streaming, deterministic semiconductor demo fixtures. Never reads SOURCE_DATABASE_URL.

Requires the workspace-created, loopback-only wafer_demo cluster and its identity
marker. --replace replaces only these seven owned demo tables, in a transaction.
No random global state: each value is a function of seed, row ID, and stream ID.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASK = (1 << 64) - 1
EPOCH = datetime(2026, 10, 1, tzinfo=timezone.utc)
DIE_SITES = [(x, y) for y in range(-15, 16) for x in range(-15, 16) if x*x + y*y <= 225]
PROFILES = {"dev": (6, 100), "perf": (60, 1400)}
PROJECTS = [(1,"P01","Logic 3 nm","Logic-A","3 nm"), (2,"P02","Memory DDR5","DRAM-B","12 nm"), (3,"P03","Power SiC","SiC-C","150 nm")]
STEPS = ["DEP", "ETCH", "CMP", "LITHO", "IMPLANT"]
METRICS = [("thickness",100.0,3.5,"nm",90.0,110.0), ("CD",28.0,1.2,"nm",24.0,32.0), ("resistance",80.0,4.0,"ohm",65.0,95.0), ("overlay",0.0,0.8,"nm",-3.0,3.0)]


def peak_working_set_mb() -> float | None:
    """Windows peak process working set, including Python/driver and COPY buffers."""
    if sys.platform!="win32": return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[("cb",wintypes.DWORD),("PageFaultCount",wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ("PeakWorkingSetSize","WorkingSetSize","QuotaPeakPagedPoolUsage","QuotaPagedPoolUsage","QuotaPeakNonPagedPoolUsage","QuotaNonPagedPoolUsage","PagefileUsage","PeakPagefileUsage")]
    counters=Counters(); counters.cb=ctypes.sizeof(counters)
    getter=ctypes.WinDLL("psapi").GetProcessMemoryInfo
    getter.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    getter.restype=wintypes.BOOL
    if not getter(wintypes.HANDLE(-1),ctypes.byref(counters),counters.cb): return None
    return round(counters.PeakWorkingSetSize/(1024*1024),3)


def uniform(seed: int, row_id: int, stream: int = 0) -> float:
    z = (seed + row_id*0x9E3779B97F4A7C15 + stream*0xD1B54A32D192ED03) & MASK
    z = ((z ^ (z >> 30))*0xBF58476D1CE4E5B9) & MASK
    z = ((z ^ (z >> 27))*0x94D049BB133111EB) & MASK
    return ((z ^ (z >> 31)) + 0.5) / (1 << 64)


def normal(seed: int, row_id: int) -> float:
    return math.sqrt(-2*math.log(max(uniform(seed,row_id,1),1e-15)))*math.cos(2*math.pi*uniform(seed,row_id,2))


def lot_row(lot_id: int) -> tuple:
    project = PROJECTS[(lot_id-1)%3]
    code = f"{project[1]}-202610-{(lot_id-1)//3+1:04d}"
    return (lot_id,project[0],code,project[3],project[4],EPOCH+timedelta(days=(lot_id-1)//3))


def wafer_row(wafer_id: int) -> tuple:
    lot = lot_row((wafer_id-1)//25+1)
    number = (wafer_id-1)%25+1
    return (wafer_id,lot[1],lot[0],lot[2],f"{lot[2]}-W{number:02d}",number,300,lot[5]+timedelta(minutes=number*20))


def fact_base(wafer: tuple, row_id: int) -> tuple:
    return (row_id,wafer[1],wafer[2],wafer[3],wafer[0],wafer[4],wafer[7])


def inline_row(seed: int, wafer: tuple, offset: int) -> tuple:
    row_id=wafer[0]*10000+offset+1
    metric,mean,std,unit,lsl,usl=METRICS[offset%4]
    step=STEPS[(offset//4)%5]
    equipment=(wafer[0]+(offset//4)%5)%6+1
    x=((offset//20)%7)-3; y=((offset//140)%7)-3
    shift=(6.0 if wafer[2]==4 else 0)+(2.0 if equipment==3 else 0)+(wafer[2]-1)*0.06
    value=round(mean+std*normal(seed,row_id)+(shift if metric=="thickness" else shift*0.1)+0.08*(x*x+y*y),6)
    if offset%97==96: value=None
    return fact_base(wafer,row_id)+(step,f"{step}-R{wafer[1]:02d}",metric,value,unit,lsl,usl,x,y,equipment)


def wat_row(seed: int, wafer: tuple, offset: int) -> tuple:
    row_id=wafer[0]*100+offset+1
    metric,mean,std,unit,lsl,usl=[("VTH",0.72,0.02,"V",0.62,0.82),("IDSAT",450.0,15.0,"uA",390.0,510.0)][offset%2]
    shift=(0.055 if wafer[2]==4 else 0)+(wafer[2]-1)*0.0006
    value=round(mean+std*normal(seed+17,row_id)+(shift if metric=="VTH" else shift*100),6)
    return fact_base(wafer,row_id)+(1,metric,value,unit,lsl,usl,(offset//2)%3-1,(offset//6)%3-1,7)


def die_row(seed: int, wafer: tuple, offset: int) -> tuple:
    row_id=wafer[0]*1000+offset+1
    x,y=DIE_SITES[offset]
    untested=uniform(seed+19,row_id,3)<0.015
    failure=0.025+(0.14 if x*x+y*y>169 else 0)+(0.17 if wafer[2]==4 else 0)
    passed=None if untested else uniform(seed+19,row_id,4)>=failure
    bin_value=None if untested else (1 if passed else 2+int(uniform(seed,row_id,5)*3))
    return fact_base(wafer,row_id)+(1,x,y,"untested" if untested else "tested",passed,bin_value)


def rows_for(table: str, seed: int, lots: int, inline_count: int):
    for wafer_id in range(1,lots*25+1):
        wafer=wafer_row(wafer_id)
        if table=="wafers": yield wafer; continue
        factory,count={"inline_measurements":(inline_row,inline_count),"wat_measurements":(wat_row,18),"die_results":(die_row,len(DIE_SITES))}[table]
        for offset in range(count): yield factory(seed,wafer,offset)


def manifest_for(seed: int, profile: str, lots: int, inline_count: int) -> dict:
    totals=defaultdict(lambda: {"tested_dies":0,"passed_dies":0})
    per_lot=defaultdict(lambda: {"tested_dies":0,"passed_dies":0})
    for row in rows_for("die_results",seed,lots,inline_count):
        for target in (totals[str(row[1])],per_lot[row[3]]):
            target["tested_dies"] += int(row[-3]=="tested")
            target["passed_dies"] += int(row[-2] is True)
    for values in list(totals.values())+list(per_lot.values()):
        values["yield_pct"]=100*values["passed_dies"]/values["tested_dies"]
    return {"generator_version":1,"seed":seed,"profile":profile,
        "counts":{"projects":3,"equipment":7,"lots":lots,"wafers":lots*25,
            "inline_measurements":lots*25*inline_count,"wat_measurements":lots*25*18,"die_results":lots*25*len(DIE_SITES)},
        "projects":dict(totals),"lots":dict(per_lot),
        "anomalies":{"lot_id":4,"lot_code":lot_row(4)[2],"inline_thickness_shift_nm":6.0,
            "eds_failure_probability_increase":0.17,"equipment_id":3,"equipment_thickness_shift_nm":2.0,
            "wafer_edge_radius":13,"edge_failure_probability_increase":0.14},
        "summary_grain":"One row per wafer; inline thickness at DEP only; WAT VTH at test_run_id=1; EDS test_run_id=1.",
        "yield_definition":"100 * sum(passed_dies) / sum(tested_dies); untested dies excluded"}


def connect_demo():
    import psycopg
    runtime=json.loads((ROOT/".local/runtime.json").read_text(encoding="utf-8-sig"))
    expected=(ROOT/".local/pgdata").resolve()
    if runtime.get("marker")!="wafer-local-demo-v1" or Path(runtime["data_dir"]).resolve()!=expected or runtime["port"]!=55432:
        raise RuntimeError("Refusing to seed an unrecognized database runtime")
    connection=psycopg.connect(host="127.0.0.1",port=55432,dbname="wafer_demo",user="wafer_local_admin",password=runtime["admin_password"])
    with connection.cursor() as cursor:
        cursor.execute("SELECT marker FROM public.demo_identity")
        if cursor.fetchone()!=("wafer-local-demo-v1",): raise RuntimeError("Missing demo database identity marker")
    return connection


def seed_database(profile: str, seed: int, replace: bool, batch_size: int) -> dict:
    lots,inline_count=PROFILES[profile]
    started=time.perf_counter()
    with connect_demo() as connection:
        with connection.cursor() as cursor:
            cursor.execute((ROOT/"demo/schema.sql").read_text())
            cursor.execute("SELECT count(*) FROM projects")
            if cursor.fetchone()[0] and not replace:
                raise RuntimeError("Demo already has data. Pass --replace explicitly to replace the owned demo fixtures.")
            if replace:
                cursor.execute("TRUNCATE inline_measurements,wat_measurements,die_results,wafers,lots,equipment,projects")
            tables={"projects":iter(PROJECTS),"equipment":iter([(i,f"EQ-{i:02d}",f"{'WAT' if i==7 else 'Process'} tool {i:02d}","test" if i==7 else "process") for i in range(1,8)]),
                "lots":(lot_row(i) for i in range(1,lots+1))}
            for table in ["projects","equipment","lots","wafers","inline_measurements","wat_measurements","die_results"]:
                stream=tables.get(table) if table in tables else rows_for(table,seed,lots,inline_count)
                count=0
                with cursor.copy(f"COPY {table} FROM STDIN WITH (FORMAT CSV)") as copy:
                    import csv,io
                    buffer=io.StringIO(); writer=csv.writer(buffer,lineterminator="\n")
                    for row in stream:
                        writer.writerow(row); count+=1
                        if count%batch_size==0:
                            copy.write(buffer.getvalue()); buffer.seek(0); buffer.truncate(0)
                    if buffer.tell(): copy.write(buffer.getvalue())
                print(f"{table}: {count:,} rows",flush=True)
            cursor.execute((ROOT/"demo/indexes.sql").read_text())
            cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO wafer_source")
        connection.commit()
        connection.autocommit=True
        connection.execute("ANALYZE")
    manifest=manifest_for(seed,profile,lots,inline_count)
    manifest["seed_elapsed_seconds"]=round(time.perf_counter()-started,3)
    manifest["generator_peak_working_set_mb"]=peak_working_set_mb()
    path=ROOT/".local"/f"manifest-{profile}.json"
    path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(f"Saved verification manifest to {path.relative_to(ROOT)}",flush=True)
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",choices=PROFILES,default="dev")
    parser.add_argument("--seed",type=int,default=20261019)
    parser.add_argument("--replace",action="store_true")
    parser.add_argument("--batch-size",type=int,default=5000)
    args=parser.parse_args()
    if not 1<=args.batch_size<=100000: parser.error("batch-size must be 1..100000")
    seed_database(args.profile,args.seed,args.replace,args.batch_size)
