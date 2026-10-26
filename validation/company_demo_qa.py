"""Isolated verification harness. Never runs the suite against the user's app DB."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local/company-demo-qa.json"


def runtime():
    result = json.loads((ROOT / ".local/runtime.json").read_text(encoding="utf-8-sig"))
    assert result["marker"] == "wafer-local-demo-v1" and result["port"] == 55432
    return result


def connect(name, *, autocommit=False):
    r = runtime()
    return psycopg.connect(host="127.0.0.1", port=r["port"], user="wafer_local_admin",
        password=r["admin_password"], dbname=name, autocommit=autocommit)


def metadata_snapshot():
    with connect("wafer_app") as c:
        c.execute("SET TRANSACTION READ ONLY")
        tables = c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        result = {}
        for (name,) in tables:
            rows = c.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(sql.Identifier("public", name))).fetchall()
            result[name] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(sorted(row[0] for row in rows)).encode()).hexdigest()}
        return result


def prepare():
    if STATE.exists():
        raise RuntimeError("QA state already exists; refusing to recreate databases")
    suffix = uuid.uuid4().hex[:10]
    state = {"metadata": "wafer_qa_app_" + suffix, "source": "wafer_qa_source_" + suffix,
             "imported": "wafer_qa_import_" + suffix, "flow_metadata": "wafer_qa_flow_" + suffix,
             "snapshot": metadata_snapshot()}
    with connect("postgres", autocommit=True) as c:
        for name in (state["metadata"], state["flow_metadata"]):
            c.execute(sql.SQL("CREATE DATABASE {} OWNER wafer_metadata").format(sql.Identifier(name)))
            c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE wafer_demo OWNER wafer_local_admin").format(sql.Identifier(state["source"])))
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(state["source"])))
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO wafer_source").format(sql.Identifier(state["source"])))
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in state.items() if k != "snapshot"}))


def environment(*, flow=False):
    from cryptography.fernet import Fernet
    state = json.loads(STATE.read_text(encoding="utf-8"))
    r = runtime()
    env = os.environ.copy()
    for variable, user, password, name in (
        ("APP_DATABASE_URL", "wafer_metadata", r["app_password"], state["flow_metadata" if flow else "metadata"]),
        ("SOURCE_DATABASE_URL", "wafer_source", r["source_password"], state["source"] if not flow else ""),
    ):
        env[variable] = URL.create("postgresql+psycopg", username=user, password=password,
            host="127.0.0.1", port=r["port"], database=name).render_as_string(hide_password=False) if name else ""
    key = ROOT / ".local/company-demo-qa.key"
    if not key.exists():
        key.write_bytes(Fernet.generate_key())
    env.update(DEMO_MODE="true", AUTH_MODE="local_admin", DB_CREDENTIALS_KEY=key.read_text(),
        LLM_CREDENTIALS_KEY=key.read_text(), KNOWLEDGE_LLM_API_KEY="", LLM_BASE_URL="", LLM_MODEL="",
        PYTHONPATH=str(ROOT / "backend") + os.pathsep + str(ROOT), PYTHONUTF8="1")
    return env


def preserve():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    after = metadata_snapshot()
    assert after == state["snapshot"], "User metadata changed; inspect before/after hashes"
    print(f"Existing app metadata preserved: {len(after)} tables, {sum(t['count'] for t in after.values())} rows")


def fresh_flow():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    name = "wafer_qa_flow_" + uuid.uuid4().hex[:10]
    with connect("postgres", autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {} OWNER wafer_metadata").format(sql.Identifier(name)))
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
    state["flow_metadata"] = name
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("New isolated flow metadata prepared:", name)


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "prepare":
        prepare()
    elif action == "preserve":
        preserve()
    elif action == "fresh-flow":
        fresh_flow()
    elif action == "serve":
        os.environ.update(environment(flow=True))
        os.environ['ALLOWED_ORIGINS'] = 'http://127.0.0.1:8001'
        sys.path.insert(0, str(ROOT / "backend"))
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=8001)
    else:
        raise SystemExit(subprocess.call([sys.executable, "-X", "utf8", *sys.argv[2:]],
            cwd=ROOT, env=environment(flow=action == "flow")))
