"""Schedule fixtures must not invent completed events after the current step."""
from datetime import datetime

from demo.fab.generator import Config, OPERATIONS
from demo.fab.schedule_generator import SCHEDULE_COLUMNS, wip_lots


def test_wip_plan_history_measurements_and_tat_agree():
    config = Config()
    seen_lots = set()
    record_ids = {}
    for rows, schedule, provenance in wip_lots(config):
        status, = schedule['current_status']
        lot_id = status['lot_id']
        assert lot_id not in seen_lots
        seen_lots.add(lot_id)
        current_step = OPERATIONS.index(status['operation'])
        assert [r['operation_seq'] for r in schedule['lot_plan']] == list(range(1, 10))
        assert [r['operation'] for r in schedule['lot_plan']] == list(OPERATIONS)
        assert [r['operation'] for r in rows['lot_his']] == list(OPERATIONS[:current_step])
        assert [r['operation'] for r in schedule['operation_tat']] == list(OPERATIONS[:current_step])
        start = datetime.fromisoformat(provenance['started_at'])
        for event, duration in zip(rows['lot_his'], schedule['operation_tat']):
            assert duration['tat'] == round((event['end_dt_tm'] - start).total_seconds() / 3600, 3)
            start = event['end_dt_tm']
        assert start < datetime.fromisoformat(provenance['current_started_at']) < config.anchor
        wafers = {r['wf_id'] for r in rows['wafer_his']}
        assert 10 <= len(wafers) <= 15 and wafers == set(range(1, len(wafers) + 1))
        wafer_history = {(r['wf_id'], r['operation']): r['end_dt_tm'] for r in rows['wafer_his']}
        rejected = {r['wf_id']: r['end_dt_tm'] for r in rows['reject_his']}
        for table, values in rows.items():
            for row in values:
                assert row['lot_id'] == lot_id and row['lot_cd'] == lot_id[:4]
                assert row['end_dt_tm'] < config.anchor
                assert row['record_id'] not in record_ids.setdefault(table, set())
                record_ids[table].add(row['record_id'])
                if table in ('inline_data', 'epm_data'):
                    assert wafer_history[row['wf_id'], row.get('operation', 'EPM')] <= row['end_dt_tm']
                if table in ('inline_data', 'epm_data', 'wafer_his') and row['wf_id'] in rejected:
                    assert row['end_dt_tm'] < rejected[row['wf_id']]
        for table, columns in SCHEDULE_COLUMNS.items():
            assert all(set(row) == set(columns) for row in schedule[table])
    assert len(seen_lots) == 18


def test_schedule_is_reproducible_and_first_step_does_not_fabricate_measurements():
    a = next(wip_lots(Config()))
    assert a == next(wip_lots(Config()))
    rows, schedule, _ = a
    assert schedule['current_status'][0]['operation'] == 'oper A CD'
    assert rows['inline_data'] == [] and rows['epm_data'] == []
    assert len(schedule['operation_tat']) == 1
