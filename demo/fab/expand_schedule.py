"""One-time, backed-up rescale of the identified local synthetic fab DB only."""
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
from psycopg import sql

from dense_schedule import route, durations
from seed import DATABASE, MARKER, ROOT, connect, runtime, api

DENSE_MARKER = 'project-manager-fab-dense-schedule-v1'
TABLES = ('lot_plan','operation_tat','current_status','lot_his','inline_data','epm_data','wafer_his','reject_his','tem_his')
BACKUP = ROOT / '.local/fab-dense-backup'


def rows_copy(connection, table, columns, rows):
    with connection.cursor().copy(sql.SQL('COPY {} ({}) FROM STDIN').format(
        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)))) as copy:
        for row in rows: copy.write_row(row)


def fingerprint(connection, table):
    return connection.execute(sql.SQL("SELECT count(*),coalesce(sum(hashtextextended((to_jsonb(t)-'end_dt_tm')::text,0)::numeric),0)::text FROM {} t").format(sql.Identifier(table))).fetchone()


def expand():
    assert api('/database-status')['database'] == DATABASE
    anchor = datetime.fromisoformat(json.loads((ROOT/'.local/fab-schedule-manifest.json').read_text(encoding='utf-8'))['anchor'])
    steps = route()
    with connect(runtime()) as connection:
        connection.execute("SET LOCAL lock_timeout='5s'")
        connection.execute("SET LOCAL statement_timeout='180s'")
        connection.execute('SELECT pg_advisory_xact_lock(2026102209)')
        assert connection.execute("SELECT shobj_description(oid,'pg_database'),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone() == (MARKER,'wafer_local_admin')
        comment = connection.execute("SELECT obj_description('public.current_status'::regclass,'pg_class')").fetchone()[0]
        if DENSE_MARKER in comment:
            report = json.loads(comment.split('; ',1)[1])
            (ROOT/'.local/fab-dense-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('Dense fixture already applied; no data changed.')
            return
        assert 'project-manager-fab-schedule-v1' in comment
        assert connection.execute('SELECT count(*) FROM lot_plan').fetchone()[0] == 702
        assert not BACKUP.exists(), 'Backup already exists; inspect the previous attempt before retrying.'
        BACKUP.mkdir()
        (BACKUP/'original-comment.txt').write_text(comment,encoding='utf-8')
        for table in TABLES:
            with gzip.open(BACKUP/f'{table}.csv.gz','wb') as output:
                with connection.cursor().copy(sql.SQL('COPY (SELECT * FROM {}) TO STDOUT WITH (FORMAT CSV, HEADER TRUE)').format(sql.Identifier(table))) as copy:
                    for block in copy: output.write(block)
        print('Nine-table backup complete.',flush=True)
        originals = {table:fingerprint(connection,table) for table in TABLES[4:]}
        history = connection.execute('SELECT lot_cd,lot_id,operation,end_dt_tm,record_id,remark FROM lot_his').fetchall()
        old = {(g,lot,op):(end,rid,remark) for g,lot,op,end,rid,remark in history}
        currents = {(g,lot):op for g,lot,op in connection.execute('SELECT lot_cd,lot_id,operation FROM current_status')}
        lots = sorted({(g,lot) for g,lot,*_ in history})
        next_id = max(h[4] for h in history)+1
        plan_rows, tat_rows, history_rows, shifts, provenance = [], [], [], [], []
        for group, lot in lots:
            days, hours = durations(lot)
            current = currents.get((group,lot))
            at_step = next(s['operation_seq'] for s in steps if s['operation']==current) if current else 1001
            if current:
                elapsed = min(.1,hours[at_step-1]*.2)
                started = anchor-timedelta(hours=elapsed+sum(hours[:at_step-1]))
            else:
                started = old[group,lot,'EPM'][0]-timedelta(hours=sum(hours))
            end = started
            for step,hours_at_step in zip(steps,hours):
                op,seq = step['operation'],step['operation_seq']
                plan_rows.append((lot,seq,op,group,step['main_oper_yn'],step['area_id'],step['samp_seq'],step['skip_yn']))
                if step['skip_yn']=='Y': continue
                end += timedelta(hours=hours_at_step)
                if seq >= at_step: continue
                tat_rows.append((lot,op,hours_at_step,group))
                prior = old.get((group,lot,op))
                rid,remark = (prior[1],prior[2]) if prior else (next_id,None)
                if not prior: next_id += 1
                history_rows.append((lot,op,remark,end,group,rid))
                if prior: shifts.append((group,lot,op,prior[0],end))
            provenance.append(dict(lot_id=lot,lot_cd=group,total_days=days,started_at=started.isoformat(),current_step=at_step if current else None))
        connection.execute("ALTER TABLE lot_plan ADD COLUMN main_oper_yn char(1) NOT NULL DEFAULT 'N' CHECK(main_oper_yn IN ('Y','N'))")
        connection.execute("ALTER TABLE lot_plan ADD COLUMN area_id text NOT NULL DEFAULT '', ADD COLUMN samp_seq integer NOT NULL DEFAULT 0, ADD COLUMN skip_yn char(1) NOT NULL DEFAULT 'N' CHECK(skip_yn IN ('Y','N'))")
        connection.execute("COMMENT ON COLUMN lot_plan.area_id IS '합성 공정 영역. VIRTUAL/CNC는 표시 대상에서 제외'")
        connection.execute("COMMENT ON COLUMN lot_plan.samp_seq IS '합성 샘플 공정 순번. 0은 일반 공정'")
        connection.execute("COMMENT ON COLUMN lot_plan.skip_yn IS 'Y이면 계획에는 존재하지만 실행·TAT 계산·표시에서 제외'")
        connection.execute("COMMENT ON COLUMN lot_plan.main_oper_yn IS '주요 공정 Y/N. 합성 계획은 1000단계 중 500단계를 Y로 표시'")
        connection.execute('CREATE TEMP TABLE dense_plan (LIKE lot_plan INCLUDING DEFAULTS) ON COMMIT DROP')
        connection.execute('CREATE TEMP TABLE dense_tat (LIKE operation_tat INCLUDING DEFAULTS) ON COMMIT DROP')
        connection.execute('CREATE TEMP TABLE dense_history (LIKE lot_his INCLUDING DEFAULTS) ON COMMIT DROP')
        connection.execute('CREATE TEMP TABLE dense_shifts(lot_cd text,lot_id text,operation text,old_end timestamptz,new_end timestamptz) ON COMMIT DROP')
        rows_copy(connection,'dense_plan',('lot_id','operation_seq','operation','lot_cd','main_oper_yn','area_id','samp_seq','skip_yn'),plan_rows)
        rows_copy(connection,'dense_tat',('lot_id','operation','tat','lot_cd'),tat_rows)
        rows_copy(connection,'dense_history',('lot_id','operation','remark','end_dt_tm','lot_cd','record_id'),history_rows)
        rows_copy(connection,'dense_shifts',('lot_cd','lot_id','operation','old_end','new_end'),shifts)
        # Existing milestone names/IDs stay stable for pivots, cards and foreign keys.
        connection.execute('UPDATE lot_plan p SET operation_seq=d.operation_seq,main_oper_yn=d.main_oper_yn,area_id=d.area_id,samp_seq=d.samp_seq,skip_yn=d.skip_yn FROM dense_plan d WHERE p.lot_cd=d.lot_cd AND p.lot_id=d.lot_id AND p.operation=d.operation')
        connection.execute('INSERT INTO lot_plan SELECT d.* FROM dense_plan d WHERE NOT EXISTS (SELECT 1 FROM lot_plan p WHERE p.lot_cd=d.lot_cd AND p.lot_id=d.lot_id AND p.operation=d.operation)')
        connection.execute('DELETE FROM operation_tat')
        connection.execute('INSERT INTO operation_tat SELECT * FROM dense_tat')
        connection.execute('UPDATE lot_his h SET end_dt_tm=d.end_dt_tm FROM dense_history d WHERE h.lot_cd=d.lot_cd AND h.record_id=d.record_id')
        connection.execute('INSERT INTO lot_his SELECT d.* FROM dense_history d WHERE NOT EXISTS (SELECT 1 FROM lot_his h WHERE h.lot_cd=d.lot_cd AND h.record_id=d.record_id)')
        print('Plan and elapsed TAT rebuilt; aligning measurement timestamps.',flush=True)
        for table in ('inline_data','wafer_his','reject_his'):
            connection.execute(sql.SQL('UPDATE {} t SET end_dt_tm=t.end_dt_tm+(d.new_end-d.old_end) FROM dense_shifts d WHERE t.lot_cd=d.lot_cd AND t.lot_id=d.lot_id AND t.operation=d.operation').format(sql.Identifier(table)))
        connection.execute("UPDATE epm_data t SET end_dt_tm=t.end_dt_tm+(d.new_end-d.old_end) FROM dense_shifts d WHERE t.lot_cd=d.lot_cd AND t.lot_id=d.lot_id AND d.operation='EPM'")
        connection.execute('UPDATE tem_his t SET end_dt_tm=t.end_dt_tm+(d.new_end-d.old_end) FROM reject_his r,dense_shifts d WHERE t.lot_cd=r.lot_cd AND t.reject_id=r.record_id AND r.lot_cd=d.lot_cd AND r.lot_id=d.lot_id AND r.operation=d.operation')
        assert all(fingerprint(connection,t)==originals[t] for t in originals), 'Measurement/history payload changed beyond timestamps'
        assert connection.execute('SELECT min(tat),max(tat) FROM operation_tat').fetchone()[0] >= 1/3
        counts = {t:connection.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0] for t in TABLES}
        report = dict(marker=DENSE_MARKER,database=DATABASE,anchor=anchor.isoformat(),steps_per_lot=1000,
                      main_steps_per_lot=500,skipped_steps_per_lot=50,active_steps_per_lot=950,
                      displayed_steps_per_lot=sum(s['skip_yn']!='Y' and s['area_id'] not in ('VIRTUAL','CNC') and s['samp_seq']==0 and s['main_oper_yn']=='Y' for s in steps),
                      tat_unit='hour',tat_range_hours=[.334,3.],total_days_range=[30,60],
                      counts=counts,lots=provenance,measurement_payload_preserved=True,
                      timestamps_retimed=True,backup=str(BACKUP),wafer_history='Original 9 milestone operations only')
        connection.execute(sql.SQL('COMMENT ON TABLE current_status IS {}').format(sql.Literal('합성 고밀도 WIP snapshot; '+json.dumps(report,ensure_ascii=False))))
        connection.execute("COMMENT ON TABLE lot_plan IS '합성 테스트: lot별 1000개 고유 공정, main_oper_yn=Y 500개, 전체 경과 시간 30~60일'")
    (ROOT/'.local/fab-dense-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with connect(runtime(),autocommit=True) as connection: connection.execute('ANALYZE')
    print(json.dumps({k:report[k] for k in ('steps_per_lot','main_steps_per_lot','tat_range_hours','total_days_range','counts')},ensure_ascii=False,indent=2))


if __name__ == '__main__': expand()
