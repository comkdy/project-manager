"""Add WIP lots without rewriting the six original measurement/history tables."""
from datetime import timedelta
import random

try:
    from .generator import GROUPS, OPERATIONS, TABLES, lot_fixture
except ImportError:  # Direct script execution.
    from generator import GROUPS, OPERATIONS, TABLES, lot_fixture

SCHEDULE_COLUMNS = {
    'lot_plan': ('lot_id', 'operation_seq', 'operation', 'lot_cd'),
    'operation_tat': ('lot_id', 'operation', 'tat', 'lot_cd'),
    'current_status': ('lot_id', 'operation', 'remark', 'lot_cd'),
}


def plan(lot_cd, lot_id):
    return [dict(lot_cd=lot_cd, lot_id=lot_id, operation_seq=i + 1, operation=op)
            for i, op in enumerate(OPERATIONS)]


def wip_lots(config):
    """18 additional lots, 10..15 wafers each, at different incomplete steps.

    Reuse the correlated measurements, missing values and scrap rules of the
    baseline generator, but move only completed steps before the snapshot.
    The generated start times are fixture provenance, not extra DB columns.
    """
    for group_index, lot_cd in enumerate(GROUPS):
        for number in range(1, 7):
            index = group_index * 6 + number - 1
            current_step = 1 + index % (len(OPERATIONS) - 1)
            lot_id = f'{lot_cd}{config.lots_per_group + number}'
            rng = random.Random(config.seed + 900001 + index)
            durations = [rng.randint(120, 240) if op.endswith(' CD') else rng.randint(240, 480)
                         for op in OPERATIONS]
            age = 60 + index * 7
            # At least one hour between the latest completed lot event and
            # current step start leaves all wafer measurements in the past.
            current_start = config.anchor - timedelta(minutes=age)
            finished = {current_step - 1: current_start - timedelta(hours=1)}
            for step in range(current_step - 2, -1, -1):
                finished[step] = finished[step + 1] - timedelta(minutes=durations[step + 1])
            template = lot_fixture(config, group_index, number)
            old_times = {r['operation']: r['end_dt_tm'] for r in template['lot_his']}
            rows = {table: [] for table in TABLES}
            id_base = 1_000_000_000_000 + index * 1_000_000

            def new_id(old):
                return id_base + old % 1_000_000

            for table in TABLES:
                for old in template[table]:
                    operation = old.get('operation', 'EPM')
                    step = OPERATIONS.index(operation)
                    if step >= current_step:
                        continue
                    at = finished[step] + (old['end_dt_tm'] - old_times[operation])
                    if at >= config.anchor:
                        continue
                    row = dict(old, lot_id=lot_id, record_id=new_id(old['record_id']), end_dt_tm=at)
                    if 'reject_id' in row:
                        row['reject_id'] = new_id(row['reject_id'])
                    rows[table].append(row)
            schedule = {
                'lot_plan': plan(lot_cd, lot_id),
                'operation_tat': [dict(lot_id=lot_id, lot_cd=lot_cd, operation=OPERATIONS[step], tat=round(durations[step] / 60, 3))
                                  for step in range(current_step)],
                'current_status': [dict(lot_id=lot_id, lot_cd=lot_cd, operation=OPERATIONS[current_step],
                                        remark='진행 중 · 합성 테스트' if index % 5 else '우선 진행 확인 · 합성 테스트')],
            }
            yield rows, schedule, dict(lot_id=lot_id, lot_cd=lot_cd,
                                       started_at=(finished[0] - timedelta(minutes=durations[0])).isoformat(),
                                       current_started_at=current_start.isoformat())
