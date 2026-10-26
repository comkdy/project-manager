"""Convert the identified local schedule fixture from minutes to hours once."""
from decimal import Decimal, ROUND_HALF_UP
import json

from psycopg import sql

from add_schedule import EXTENSION_MARKER, REPORT_PATH
from seed import DATABASE, MARKER, ROOT, connect, runtime


def migrate():
    changed = 0
    with connect(runtime()) as connection:
        connection.execute("SET LOCAL lock_timeout='5s'")
        connection.execute("SET LOCAL statement_timeout='30s'")
        connection.execute('SELECT pg_advisory_xact_lock(2026102209)')
        identity = connection.execute("SELECT shobj_description(oid,'pg_database'), pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        assert identity == (MARKER, 'wafer_local_admin'), 'Unrecognized fixture database'
        connection.execute('LOCK TABLE public.operation_tat IN SHARE ROW EXCLUSIVE MODE')
        comment = connection.execute("SELECT obj_description('public.current_status'::regclass,'pg_class')").fetchone()[0]
        prefix, payload = comment.split('; ', 1)
        manifest = json.loads(payload)
        assert manifest['marker'] == EXTENSION_MARKER and manifest['database'] == DATABASE
        assert manifest['tat_unit'] in ('minute', 'hour'), 'Unknown TAT unit'
        if manifest['tat_unit'] == 'minute':
            before = connection.execute('SELECT lot_cd,lot_id,operation,tat FROM operation_tat ORDER BY lot_cd,lot_id,operation').fetchall()
            assert len(before) == manifest['counts']['operation_tat']
            backup = ROOT / '.local/fab-tat-minutes-backup.json'
            if not backup.exists():
                backup.write_text(json.dumps({'tat_unit': 'minute', 'rows': before}, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
            changed = connection.execute('UPDATE public.operation_tat SET tat=round(tat/60,3)').rowcount
            after = connection.execute('SELECT lot_cd,lot_id,operation,tat FROM operation_tat ORDER BY lot_cd,lot_id,operation').fetchall()
            assert len(after) == len(before)
            for old, new in zip(before, after):
                assert old[:3] == new[:3]
                assert new[3] == (old[3] / Decimal(60)).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
            manifest['tat_unit'] = 'hour'
            connection.execute(sql.SQL('COMMENT ON TABLE public.current_status IS {}').format(
                sql.Literal(prefix + '; ' + json.dumps(manifest, ensure_ascii=False))))
        connection.execute("COMMENT ON TABLE public.operation_tat IS '합성 테스트: 완료한 공정의 경과 시간(hour), 대기 포함. 현재/미래 공정은 포함하지 않음'")
        connection.execute("COMMENT ON COLUMN public.operation_tat.tat IS '단위: 시간(hour), 소수 셋째 자리. 이전 공정 완료부터 해당 공정 완료까지의 경과 시간. 첫 공정은 시작을 아는 합성 WIP만 제공'")
    # Recover the sidecar even when a previous run committed but file writing failed.
    REPORT_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'database': DATABASE, 'tat_unit': 'hour', 'converted_rows': changed}))


if __name__ == '__main__':
    migrate()
