"""Domain invariants that distinguish this fixture from independent random rows."""
from datetime import datetime, timezone

from demo.fab.generator import COLUMNS, Config, SITES, TABLES, lot_fixture


def test_equipment_assignment_is_reproducible_without_changing_measurements(monkeypatch):
    from demo.fab import generator
    assigned = lot_fixture(Config(), 0, 6)
    assert len({r['eqp_id'] for r in assigned['wafer_his'] if r['operation']=='oper B'}) == 3
    assert all(r['eqp_id'].startswith('SYN_') for r in assigned['wafer_his'])
    monkeypatch.setattr(generator, 'equipment_id', lambda *args: 'unused')
    unassigned = lot_fixture(Config(), 0, 6)
    for table in TABLES:
        assert [{k:v for k,v in r.items() if k!='eqp_id'} for r in assigned[table]] == [
                {k:v for k,v in r.items() if k!='eqp_id'} for r in unassigned[table]]


def test_schema_determinism_and_local_wafer_identity():
    config = Config(lots_per_group=20, anchor=datetime(2026, 10, 22, tzinfo=timezone.utc))
    a = lot_fixture(config, 0, 6)
    assert a == lot_fixture(config, 0, 6)
    assert len(SITES) == 49
    assert set(a) == set(TABLES)
    for table, rows in a.items():
        assert all(set(row) == set(COLUMNS[table]) for row in rows)
        assert all(row['lot_cd'] == row['lot_id'][:4] == 'XXXA' for row in rows)
        assert len({row['record_id'] for row in rows}) == len(rows)
    wafers = {r['wf_id'] for r in a['wafer_his']}
    assert wafers == set(range(1, 11))
    b = lot_fixture(config, 1, 6)
    assert {r['wf_id'] for r in b['wafer_his']}.intersection(wafers)
    assert a['inline_data'][0]['lot_id'] != b['inline_data'][0]['lot_id']


def test_measurement_times_repeat_nulls_and_wafer_process_coherence():
    data = lot_fixture(Config(), 0, 4)
    history = {(r['lot_id'], r['wf_id'], r['operation']): r['end_dt_tm'] for r in data['wafer_his']}
    rejection = {(r['lot_id'], r['wf_id']): r['end_dt_tm'] for r in data['reject_his']}
    for table in ('inline_data', 'epm_data'):
        for row in data[table]:
            operation = row.get('operation', 'EPM')
            assert history[row['lot_id'], row['wf_id'], operation] <= row['end_dt_tm']
            if (row['lot_id'], row['wf_id']) in rejection:
                assert row['end_dt_tm'] < rejection[row['lot_id'], row['wf_id']]
    rejected_wf = data['reject_his'][0]['wf_id']
    assert not any(r['wf_id'] == rejected_wf for r in data['epm_data'])
    assert any(r['value'] is None for r in data['inline_data'])
    grain = lambda row: tuple(row[k] for k in ('lot_id', 'wf_id', 'die_x', 'die_y', 'operation', 'parameter'))
    assert len({grain(row) for row in data['inline_data']}) < len(data['inline_data'])
    a = next(r for r in data['inline_data'] if r['operation'] == 'oper A CD')
    d = next(r for r in data['inline_data'] if r['operation'] == 'oper D CD')
    assert a['end_dt_tm'] < d['end_dt_tm']


def test_tem_has_real_rejection_link_multiple_specimens_and_repeat_imaging():
    data = lot_fixture(Config(), 0, 6)
    rejects = {r['record_id']: r for r in data['reject_his']}
    assert len(data['tem_his']) == 4
    assert len({r['serial_num'] for r in data['tem_his']}) == 2
    for row in data['tem_his']:
        reject = rejects[row['reject_id']]
        assert reject['reject_reason'] == 'TEM 시편 제작'
        assert (row['lot_id'], row['wf_id']) == (reject['lot_id'], reject['wf_id'])
        assert row['end_dt_tm'] > reject['end_dt_tm']
    pending = lot_fixture(Config(), 0, 9)
    assert any(r['reject_reason'] == 'TEM 시편 제작' for r in pending['reject_his'])
    assert pending['tem_his'] == []
