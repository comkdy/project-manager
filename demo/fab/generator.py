"""Deterministic synthetic fab data, bounded to one lot in memory.

These distributions/recipes are test assumptions, not production definitions.
No source DB, credentials or application state are accessed by this module.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import random


GROUPS = ('XXXA', 'XXXB', 'XXXC')
TABLES = ('inline_data', 'epm_data', 'lot_his', 'wafer_his', 'reject_his', 'tem_his')
PARAMETERS = ('MAIN', 'SUB1', 'SUB2')
EPM_PARAMETERS = ('PARA1', 'PARA2', 'PARA3', 'PARA4', 'PARA5')
OPERATIONS = ('oper A', 'oper A CD', 'oper B', 'oper B CD',
              'oper C', 'oper C CD', 'oper D', 'oper D CD', 'EPM')
SITES = tuple((x, y) for x in range(-4, 5) for y in range(-4, 5) if x*x + y*y <= 16)
COLUMNS = {
    'inline_data': ('lot_id', 'wf_id', 'die_x', 'die_y', 'operation', 'parameter', 'value', 'end_dt_tm', 'lot_cd', 'record_id'),
    'epm_data': ('lot_id', 'wf_id', 'die_x', 'die_y', 'parameter', 'value', 'end_dt_tm', 'lot_cd', 'record_id'),
    'lot_his': ('lot_id', 'operation', 'remark', 'end_dt_tm', 'lot_cd', 'record_id'),
    'wafer_his': ('lot_id', 'wf_id', 'operation', 'recipe', 'end_dt_tm', 'lot_cd', 'record_id', 'eqp_id'),
    'reject_his': ('lot_id', 'wf_id', 'operation', 'reject_reason', 'remark', 'end_dt_tm', 'lot_cd', 'record_id'),
    'tem_his': ('lot_id', 'wf_id', 'reject_id', 'serial_num', 'end_dt_tm', 'lot_cd', 'record_id'),
}


@dataclass(frozen=True)
class Config:
    seed: int = 20261022
    lots_per_group: int = 20
    # Latest event is at least one day before this anchor; timestamps never
    # depend on row iteration time. Persist the anchor in the manifest.
    anchor: datetime = datetime(2026, 10, 22, tzinfo=timezone.utc)


def equipment_id(lot_id, wf_id, operation):
    """Synthetic equipment assignment; independent of RNG and measured outcomes."""
    key = f'{lot_id}|{wf_id}|{operation}'.encode('utf-8')
    unit = int(hashlib.md5(key).hexdigest()[:8], 16) % 3 + 1
    return f'SYN_{operation.replace(" ", "_")}_EQP{unit:02d}'


def lot_fixture(config, group_index, lot_number):
    ordinal = group_index * config.lots_per_group + lot_number
    rng = random.Random(config.seed + ordinal * 100003)
    lot_cd = GROUPS[group_index]
    lot_id = f'{lot_cd}{lot_number}'
    wafer_count = 10 + (lot_number + group_index) % 6
    # Spread lot starts over eight weeks, including data outside the dashboard's
    # recent-four-week window. Leave five days for process / rejection / imaging.
    days_back = round((config.lots_per_group - lot_number) * 55 / max(1, config.lots_per_group - 1))
    start = config.anchor - timedelta(days=5 + days_back, hours=group_index * 2)
    rows = {table: [] for table in TABLES}
    sequence = defaultdict(int)

    def add(table, **fields):
        sequence[table] += 1
        row = dict(lot_cd=lot_cd, lot_id=lot_id, record_id=ordinal * 1_000_000 + sequence[table], **fields)
        rows[table].append(row)
        return row

    lot_bias = rng.gauss(0, .35) + (2.0 if lot_cd == 'XXXB' and lot_number == 7 else 0)
    for step, operation in enumerate(OPERATIONS):
        remark = None
        if step == 4 and lot_number % 7 == 0:
            remark = '재진행 확인: 측정 편차 점검'
        elif step == 0 and lot_number % 5 == 0:
            remark = '평가용 split lot'
        elif step == 6 and lot_cd == 'XXXB' and lot_number == 7:
            remark = '특성 이동 관찰: 후속 EPM 및 TEM 비교 대상'
        add('lot_his', operation=operation, remark=remark,
            end_dt_tm=start + timedelta(hours=step * 6))

    for wf_id in range(1, wafer_count + 1):
        # Early scrap leaves genuine missing downstream measurements. TEM
        # materials complete EPM first so electrical/inline comparisons exist.
        scrap = wf_id == wafer_count and lot_number % 4 == 0
        tem = wf_id == 1 and lot_number % 3 == 0
        wafer_bias = rng.gauss(0, .2)
        stop_step = 4 if scrap else 8
        for step, operation in enumerate(OPERATIONS):
            if step > stop_step:
                continue
            passed_at = start + timedelta(hours=step * 6, minutes=wf_id * 2)
            recipe_no = 1 + (group_index + step // 2 + wf_id % 3) % 3
            add('wafer_his', wf_id=wf_id, operation=operation,
                recipe=f'RCP{recipe_no}', end_dt_tm=passed_at, eqp_id=equipment_id(lot_id, wf_id, operation))
            if operation.endswith(' CD'):
                op_index = step // 2
                for site_index, (die_x, die_y) in enumerate(SITES):
                    for param_index, parameter in enumerate(PARAMETERS):
                        # Small, deterministic absent groups (not NULL rows).
                        if (ordinal * 7 + wf_id * 11 + site_index * 3 + step + param_index) % 251 == 0:
                            continue
                        value = round(30 + op_index * 2 + param_index * .7 + group_index * .3
                                      + lot_bias + wafer_bias + (die_x**2 + die_y**2) * .035
                                      + (recipe_no - 2) * .08 + rng.gauss(0, .12), 6)
                        measured_at = passed_at + timedelta(seconds=site_index * 3 + param_index)
                        base = dict(wf_id=wf_id, die_x=die_x, die_y=die_y, operation=operation, parameter=parameter)
                        value = None if rng.random() < .002 else value
                        add('inline_data', **base, value=value, end_dt_tm=measured_at)
                        repeat_code = ordinal + wf_id + site_index + step + param_index
                        if repeat_code % 17 == 0:
                            # Some same-time observations need record_id ordering.
                            later = measured_at + timedelta(minutes=0 if repeat_code % 51 == 0 else 20)
                            last = None if repeat_code % 119 == 0 else (None if value is None else round(value + rng.gauss(0, .08), 6))
                            add('inline_data', **base, value=last, end_dt_tm=later)
            elif operation == 'EPM':
                for site_index, (die_x, die_y) in enumerate(SITES):
                    for param_index, parameter in enumerate(EPM_PARAMETERS):
                        if (ordinal + wf_id * 3 + site_index + param_index) % 311 == 0:
                            continue
                        latent = lot_bias + wafer_bias + (die_x**2 + die_y**2) * .035
                        mean, scale = ((.72, .02), (450, 15), (1.4, .1), (80, 4), (12, .8))[param_index]
                        value = round(mean + scale * (latent + rng.gauss(0, .25)), 6)
                        measured_at = passed_at + timedelta(seconds=site_index * 5 + param_index)
                        base = dict(wf_id=wf_id, die_x=die_x, die_y=die_y, parameter=parameter)
                        add('epm_data', **base, value=None if rng.random() < .002 else value, end_dt_tm=measured_at)
                        if (ordinal + wf_id + site_index + param_index) % 29 == 0:
                            add('epm_data', **base, value=round(value + scale * rng.gauss(0, .1), 6),
                                end_dt_tm=measured_at + timedelta(minutes=35))
        if scrap or tem:
            rejected_at = start + timedelta(hours=stop_step * 6 + 2, minutes=wf_id * 2)
            rejected = add('reject_his', wf_id=wf_id, operation=OPERATIONS[stop_step],
                           reject_reason='단순 폐기' if scrap else 'TEM 시편 제작',
                           remark='공정 중 손상 확인' if scrap else '단면 분석용 시편 의뢰', end_dt_tm=rejected_at)
            if tem and lot_number % 9 != 0:
                # Some TEM requests have no imaging yet; some wafers have two
                # specimens; some specimens have a later second imaging event.
                for sample in range(1, 3 if lot_number % 6 == 0 else 2):
                    serial = f'TEM-{lot_id}-W{wf_id:02d}-S{sample:02d}'
                    shot_at = rejected_at + timedelta(hours=8 + sample)
                    add('tem_his', wf_id=wf_id, reject_id=rejected['record_id'], serial_num=serial, end_dt_tm=shot_at)
                    if lot_number % 6 == 0:
                        add('tem_his', wf_id=wf_id, reject_id=rejected['record_id'], serial_num=serial,
                            end_dt_tm=shot_at + timedelta(hours=6))
    return rows


def lots(config):
    for group_index in range(len(GROUPS)):
        for number in range(1, config.lots_per_group + 1):
            yield lot_fixture(config, group_index, number)
