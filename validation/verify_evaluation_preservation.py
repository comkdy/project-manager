"""Fingerprint live metadata without exposing values or changing application rows."""
import json
from pathlib import Path
import sys

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
mode = sys.argv[1]
assert mode in {"before", "after"}
prefix = sys.argv[2] if len(sys.argv) > 2 else "evaluation"
allowed_empty = set()
extra = sys.argv[3:]
while extra:
    if len(extra) < 2 or extra[0] != '--allow-empty-table':
        raise ValueError('Unsupported preservation option')
    allowed_empty.add(extra[1])
    extra = extra[2:]
assert prefix.replace("-", "").isalnum() and len(prefix) <= 60
runtime = json.loads((ROOT / ".local/runtime.json").read_text(encoding="utf-8-sig"))
assert runtime["marker"] == "wafer-local-demo-v1" and runtime["port"] == 55432
with psycopg.connect(host="127.0.0.1", port=runtime["port"], dbname="wafer_app",
                     user="wafer_metadata", password=runtime["app_password"]) as db:
    db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    names = db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                       "AND starts_with(tablename,'app_') ORDER BY tablename").fetchall()
    result = {}
    for (name,) in names:
        count, digest = db.execute(sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(h,'' ORDER BY h),'')) "
            "FROM (SELECT md5(to_jsonb(t)::text) h FROM public.{} t) rows"
        ).format(sql.Identifier(name))).fetchone()
        result[name] = {"rows": count, "digest": digest}
baseline = ROOT / f"validation/{prefix}-metadata-before.json"
if mode == "before":
    if baseline.exists():
        raise RuntimeError("Baseline already exists; refusing overwrite")
    baseline.write_text(json.dumps(result, indent=2), encoding="utf-8")
else:
    previous = json.loads(baseline.read_text(encoding="utf-8"))
    added = set(result) - set(previous)
    changed = [name for name in sorted(set(previous) | set(result)) if previous.get(name) != result.get(name)
               and not (name in added & allowed_empty and result[name]['rows'] == 0)]
    (ROOT / f"validation/{prefix}-metadata-after.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert not changed, "Metadata changed: " + ", ".join(changed)
print(json.dumps({"tables": len(result), "rows": sum(v["rows"] for v in result.values()),
                  "unchanged": mode == "after"}))
