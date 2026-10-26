"""Add synthetic equipment ONLY to the marked local wafer_fab_demo fixture.

Existing columns are fingerprinted in the same transaction. No measurement
outcomes are generated or modified. Re-running verifies and fills NULLs only.
"""
import json
from pathlib import Path

from psycopg import sql

from generator import COLUMNS, equipment_id
from seed import DATABASE, MARKER, ROOT, api, connect, runtime

COMMENT = 'Synthetic verification equipment ID; independent of measured outcomes; not a real equipment history.'


def fingerprint(conn):
    columns = [c for c in COLUMNS['wafer_his'] if c != 'eqp_id']
    return conn.execute(sql.SQL('''SELECT count(*),
        coalesce(sum(hashtextextended(row_to_json(t)::text,0)::numeric),0)::text
        FROM (SELECT {} FROM public.wafer_his) t''').format(
            sql.SQL(',').join(map(sql.Identifier, columns)))).fetchone()


def migrate():
    assert api('/database-status')['database'] == DATABASE, 'Activate the local synthetic fixture first'
    with connect(runtime()) as conn:
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='60s'")
        conn.execute('SELECT pg_advisory_xact_lock(2026102501)')
        identity = conn.execute("SELECT shobj_description(oid,'pg_database'),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        assert identity == (MARKER, 'wafer_local_admin'), 'Unrecognized fixture database'
        before = fingerprint(conn)
        column = conn.execute("SELECT col_description(attrelid,attnum) FROM pg_attribute WHERE attrelid='public.wafer_his'::regclass AND attname='eqp_id' AND NOT attisdropped").fetchone()
        if column is None:
            conn.execute('ALTER TABLE public.wafer_his ADD COLUMN eqp_id text')
            conn.execute(sql.SQL('COMMENT ON COLUMN public.wafer_his.eqp_id IS {}').format(sql.Literal(COMMENT)))
        else:
            assert column[0] == COMMENT, 'An existing equipment column is not owned by this fixture extension'
        # No raw measurement rows are read. This query uses only source identity fields.
        identities = conn.execute('SELECT DISTINCT lot_id,wf_id,operation FROM public.wafer_his WHERE eqp_id IS NULL').fetchall()
        with conn.cursor() as cur:
            cur.executemany('UPDATE public.wafer_his SET eqp_id=%s WHERE lot_id=%s AND wf_id=%s AND operation=%s AND eqp_id IS NULL',
                [(equipment_id(lot, wafer, operation), lot, wafer, operation) for lot, wafer, operation in identities])
        conn.execute('ALTER TABLE public.wafer_his ALTER COLUMN eqp_id SET NOT NULL')
        after = fingerprint(conn)
        assert after == before, 'Existing wafer history was changed; rolling back'
        groups = conn.execute('SELECT lot_cd, count(*), count(DISTINCT operation), count(DISTINCT eqp_id), count(*) FILTER (WHERE eqp_id IS NULL) FROM public.wafer_his GROUP BY lot_cd ORDER BY lot_cd').fetchall()
        report = {'database': DATABASE, 'column': 'wafer_his.eqp_id', 'synthetic': True,
                  'updated_identities': len(identities), 'original_columns_before': before,
                  'original_columns_after': after, 'original_values_preserved': True,
                  'groups': [dict(zip(('lot_cd','rows','operations','equipment_ids','nulls'), row)) for row in groups]}
    target = ROOT / 'validation/wafer-equipment-migration.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    migrate()
