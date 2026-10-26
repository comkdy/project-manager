"""Deterministic 1,000-step plans; durations are elapsed hours, not machine uptime."""
import hashlib
import random

MILESTONES = {100: 'oper A', 200: 'oper A CD', 300: 'oper B', 400: 'oper B CD',
              500: 'oper C', 600: 'oper C CD', 700: 'oper D', 800: 'oper D CD', 1000: 'EPM'}
FAMILIES = ('PHOTO', 'ETCH', 'CLEAN', 'DEPOSITION', 'IMPLANT', 'DIFFUSION', 'CMP', 'METRO')


def route():
    eligible = [i for i in range(1,1001) if i not in MILESTONES]
    main = set(MILESTONES) | set(random.Random(20261022).sample(eligible,500-len(MILESTONES)))
    skipped = set(random.Random(20261023).sample(eligible,50))
    return [dict(operation_seq=i, operation=MILESTONES.get(i, f'P{i:04d} {FAMILIES[(i-1)%8]}'),
                 area_id=('METRO' if MILESTONES[i].endswith(' CD') else 'EPM' if i==1000 else 'PROCESS') if i in MILESTONES else ('VIRTUAL' if i%19==0 else 'CNC' if i%19==1 else FAMILIES[(i-1)%8]),
                 samp_seq=0 if i in MILESTONES or i%11 else 1,
                 skip_yn='Y' if i in skipped else 'N',
                 main_oper_yn='Y' if i in main else 'N') for i in range(1, 1001)]


def durations(lot_id):
    seed = int(hashlib.sha256(lot_id.encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    days = 30 + seed % 31
    active = [s['skip_yn'] != 'Y' for s in route()]
    weights = [rng.uniform(.3, 2.8) * (.65, 1.2, .45, 1.3, .8, 1.7, .7, 1.)[i % 8]
               for i in range(1000)]
    target = days * 24 * 1000
    lo, hi = 0., 20.
    for _ in range(80):
        middle = (lo + hi) / 2
        total = sum(max(334, min(3000, weight * middle * 1000)) for weight, keep in zip(weights,active) if keep)
        if total < target: lo = middle
        else: hi = middle
    values = [round(max(334, min(3000, weight * hi * 1000))) if keep else 0 for weight,keep in zip(weights,active)]
    diff = target - sum(values)
    for i in range(len(values)):
        if not active[i]: continue
        if not diff: break
        change = min(diff, 3000-values[i]) if diff > 0 else max(diff, 334-values[i])
        values[i] += change
        diff -= change
    assert sum(values) == target
    return days, [v / 1000 for v in values]
