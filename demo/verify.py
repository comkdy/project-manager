"""Verify demo grain, weighted yields, profile determinism, role isolation and SQL timings."""
from __future__ import annotations

import argparse
import json
import math
import time

from seed import ROOT, PROFILES, connect_demo, inline_row, wat_row, die_row, wafer_row


def verify(profile: str) -> dict:
    import psycopg
    manifest=json.loads((ROOT/".local"/f"manifest-{profile}.json").read_text())
    checks=[]; benchmarks=[]
    with connect_demo() as connection:
        with connection.cursor() as cursor:
            for table,expected in manifest["counts"].items():
                cursor.execute(f"SELECT count(*) FROM {table}")
                actual=cursor.fetchone()[0]
                assert actual==expected,(table,actual,expected)
            checks.append("all row counts equal the deterministic manifest")
            cursor.execute("SELECT project_id,sum(tested_dies),sum(passed_dies),100.0*sum(passed_dies)/nullif(sum(tested_dies),0) FROM wafer_summary GROUP BY project_id")
            for project_id,tested,passed,yield_pct in cursor.fetchall():
                expected=manifest["projects"][str(project_id)]
                assert tested==expected["tested_dies"] and passed==expected["passed_dies"]
                assert math.isclose(float(yield_pct),expected["yield_pct"],abs_tol=1e-9)
            checks.append("summary weighted yield matches independently generated tested/PASS counts")
            cursor.execute("SELECT count(*),count(DISTINCT id) FROM wafer_summary")
            count,distinct=cursor.fetchone()
            assert count==distinct==manifest["counts"]["wafers"]
            checks.append("summary has exactly one row per wafer with no measurement JOIN multiplication")
            for table,factory,offset in [("inline_measurements",inline_row,0),("inline_measurements",inline_row,96),("wat_measurements",wat_row,0),("die_results",die_row,50)]:
                for wafer_id in (1,26,76,150):
                    expected=factory(manifest["seed"],wafer_row(wafer_id),offset)
                    cursor.execute(f"SELECT * FROM {table} WHERE id=%s",(expected[0],))
                    assert cursor.fetchone()==expected,(table,wafer_id,offset)
            checks.append("shared dev/perf IDs reproduce identical values including NULLs")
            queries={
                "weighted_project_yield":"SELECT project_id,100.0*sum(passed_dies)/nullif(sum(tested_dies),0) FROM wafer_summary GROUP BY project_id",
                "lot_yield_trend":"SELECT lot_code,100.0*sum(passed_dies)/nullif(sum(tested_dies),0) FROM wafer_summary GROUP BY lot_code ORDER BY lot_code",
                "inline_equipment_distribution":"SELECT equipment_id,avg(value),min(value),max(value) FROM inline_measurements WHERE project_id=1 AND metric_name='thickness' GROUP BY equipment_id",
                "wafer_correlation":"SELECT inline_thickness,wat_vth FROM wafer_summary WHERE project_id=1 ORDER BY id LIMIT 20000",
                "wafer_map":"SELECT die_x,die_y,CASE WHEN status='untested' THEN NULL WHEN passed THEN 1 ELSE 0 END FROM die_results WHERE wafer_id=1 AND test_run_id=1",
                "keyset_page_1":"SELECT id,value FROM inline_measurements WHERE project_id=1 ORDER BY id LIMIT 201",
                "keyset_page_later":"SELECT id,value FROM inline_measurements WHERE project_id=1 AND id>1000099 ORDER BY id LIMIT 201",
            }
            for name,query in queries.items():
                cursor.execute("SET LOCAL statement_timeout='30s'")
                start=time.perf_counter(); cursor.execute(query); rows=cursor.fetchall(); elapsed=time.perf_counter()-start
                cursor.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+query)
                plan=cursor.fetchone()[0][0]
                benchmarks.append({"name":name,"elapsed_ms":round(elapsed*1000,3),"returned_rows":len(rows),"plan":plan})
    runtime=json.loads((ROOT/".local/runtime.json").read_text(encoding="utf-8-sig"))
    with psycopg.connect(host="127.0.0.1",port=55432,dbname="wafer_demo",user="wafer_source",password=runtime["source_password"]) as connection:
        assert connection.execute("SELECT count(*) FROM wafer_summary").fetchone()[0]==manifest["counts"]["wafers"]
        try:
            connection.execute("UPDATE projects SET name=name WHERE id=1")
        except psycopg.Error as error:
            assert error.sqlstate in ("25006","42501")
            connection.rollback()
        else: raise AssertionError("source role unexpectedly wrote to original table")
    for role,password,database in [("wafer_metadata",runtime["app_password"],"wafer_demo"),("wafer_source",runtime["source_password"],"wafer_app")]:
        try:
            connection=psycopg.connect(host="127.0.0.1",port=55432,dbname=database,user=role,password=password)
        except psycopg.Error as error:
            # libpq connection failures may not preserve the server SQLSTATE.
            assert error.sqlstate=="42501" or (f'permission denied for database "{database}"' in str(error) and 'CONNECT privilege' in str(error)),str(error)
        else:
            connection.close(); raise AssertionError(f"{role} unexpectedly connected to {database}")
    checks.append("source role cannot UPDATE; source and metadata database roles cannot cross-connect")
    result={"profile":profile,"checks":checks,"benchmarks":benchmarks,"target":"uncached application query <= 3000 ms; database buffer cache may be warm","row_counts":manifest["counts"]}
    path=ROOT/".local"/f"verification-{profile}.json"
    path.write_text(json.dumps(result,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"profile":profile,"checks":checks,"timings_ms":{item["name"]:item["elapsed_ms"] for item in benchmarks}},indent=2))
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--profile",choices=PROFILES,default="dev")
    verify(parser.parse_args().profile)
