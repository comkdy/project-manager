"""Create a new, identified local fixture DB without changing the original demo.

This program has no arbitrary connection URL or overwrite option. It can only
write wafer_fab_demo inside this workspace's managed local PostgreSQL runtime.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
from psycopg import sql

from generator import COLUMNS, Config, GROUPS, TABLES, lots

ROOT = Path(__file__).resolve().parents[2]
DATABASE = 'wafer_fab_demo'
MARKER = 'project-manager-fab-fixture-v1'


def runtime():
    data = json.loads((ROOT / '.local/runtime.json').read_text(encoding='utf-8-sig'))
    if (data.get('marker') != 'wafer-local-demo-v1' or data.get('port') != 55432
            or Path(data['data_dir']).resolve() != (ROOT / '.local/pgdata').resolve()
            or Path(data['bin_dir']).resolve() != (ROOT / '.local/pgsql/bin').resolve()):
        raise RuntimeError('Refusing an unrecognized local database runtime')
    return data


def connect(data, database=DATABASE, *, reader=False, autocommit=False):
    if database not in {DATABASE, 'postgres'}:
        raise RuntimeError('Unexpected fixture database')
    return psycopg.connect(host='127.0.0.1', port=data['port'], dbname=database,
                          user='wafer_source' if reader else 'wafer_local_admin',
                          password=data['source_password'] if reader else data['admin_password'],
                          autocommit=autocommit, connect_timeout=5)


def api(path, body=None):
    request = Request('http://127.0.0.1:8000/api' + path,
                      data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'Local app request failed: {error.code} {path}') from None


def create(config):
    data = runtime()
    started = time.perf_counter()
    with connect(data, 'postgres', autocommit=True) as connection:
        found = connection.execute("SELECT shobj_description(oid, 'pg_database'), pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone()
        if found is None:
            connection.execute(sql.SQL('CREATE DATABASE {} OWNER wafer_local_admin').format(sql.Identifier(DATABASE)))
            connection.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(DATABASE), sql.Literal(MARKER)))
        elif found != (MARKER, 'wafer_local_admin'):
            raise RuntimeError('Refusing to write an existing database without the exact fixture identity')
        connection.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(DATABASE)))
        connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO wafer_source').format(sql.Identifier(DATABASE)))
        connection.execute(sql.SQL('REVOKE CREATE,TEMPORARY ON DATABASE {} FROM wafer_source').format(sql.Identifier(DATABASE)))
    counts = {table: 0 for table in TABLES}
    with connect(data) as connection:
        found = connection.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m')").fetchone()[0]
        if found:
            raise RuntimeError('Fixture database already contains tables; no data was replaced. Use verify.py to inspect it.')
        connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        connection.execute('REVOKE ALL ON SCHEMA public FROM wafer_source')
        connection.execute('GRANT USAGE ON SCHEMA public TO wafer_source')
        connection.execute((Path(__file__).parent / 'schema.sql').read_text(encoding='utf-8'))
        for table in TABLES:
            for group in GROUPS:
                connection.execute(sql.SQL('CREATE TABLE public.{} PARTITION OF public.{} FOR VALUES IN ({})').format(
                    sql.Identifier(f'{table}_{group.lower()}'), sql.Identifier(table), sql.Literal(group)))
            connection.execute(sql.SQL('COMMENT ON COLUMN public.{}.record_id IS {}').format(
                sql.Identifier(table), sql.Literal('합성 이력 고유 순번. 동일 end_dt_tm이면 큰 record_id가 마지막 기록')))
        for index, lot in enumerate(lots(config), 1):
            for table in TABLES:
                if not lot[table]:
                    continue
                command = sql.SQL('COPY public.{} ({}) FROM STDIN').format(
                    sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, COLUMNS[table])))
                with connection.cursor() as cursor:
                    with cursor.copy(command) as copy:
                        for row in lot[table]:
                            copy.write_row(tuple(row[column] for column in COLUMNS[table]))
                counts[table] += len(lot[table])
            if index % 10 == 0:
                print(f'Generated {index}/{config.lots_per_group * len(GROUPS)} lots', flush=True)
        connection.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO wafer_source')
        connection.execute('REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA public FROM wafer_source')
        connection.execute('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM wafer_source')
    with connect(data, autocommit=True) as connection:
        connection.execute('ANALYZE')
    manifest = {'marker': MARKER, 'database': DATABASE, 'seed': config.seed,
                'lots_per_group': config.lots_per_group, 'anchor': config.anchor.isoformat(),
                'lot_groups': GROUPS, 'lot_count': config.lots_per_group * len(GROUPS),
                'sites_per_wafer': 49, 'counts': counts,
                'elapsed_seconds': round(time.perf_counter() - started, 2),
                'synthetic': True, 'additional_columns': ['record_id'],
                'grain': 'One row per measurement/history event; wf_id is local to lot_id.'}
    (ROOT / '.local/fab-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return manifest


def activate():
    data = runtime()
    before = {'registrations': api('/admin/databases'), 'bootstrap': api('/bootstrap'), 'dashboard': api('/dashboards')}
    (ROOT / '.local/fab-previous-workspace.json').write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
    result = api('/admin/databases', {'host': '127.0.0.1', 'port': data['port'], 'database': DATABASE,
                                    'username': 'wafer_source', 'password': data['source_password'], 'sslmode': 'prefer'})
    mounted = api(f"/admin/databases/{result['profile_id']}/activate", {})
    assert mounted['active'] and mounted['status'] == 'connected'
    print(json.dumps({'database': DATABASE, 'active': True, 'logical_tables': mounted['table_count']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261022)
    parser.add_argument('--lots-per-group', type=int, default=20)
    parser.add_argument('--anchor', default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument('--activate', action='store_true', help='Register and activate the new DB through local app admin APIs')
    parser.add_argument('--activate-existing', action='store_true', help='Only register/activate an already verified fixture DB')
    args = parser.parse_args()
    if not 1 <= args.lots_per_group <= 1000:
        parser.error('lots-per-group must be 1..1000')
    try:
        if not args.activate_existing:
            config = Config(args.seed, args.lots_per_group, datetime.fromisoformat(args.anchor).replace(tzinfo=timezone.utc))
            create(config)
        if args.activate or args.activate_existing:
            # Verify the DB before exposing it as the active analysis workspace.
            from verify import verify
            verify()
            activate()
    except Exception as error:
        # Do not echo driver errors or connection parameters containing secrets.
        print(f'Fab fixture task failed ({type(error).__name__}). Existing demo DB was not replaced.', file=sys.stderr)
        if isinstance(error, (RuntimeError, AssertionError)):
            print(str(error), file=sys.stderr)
        sys.exit(1)
