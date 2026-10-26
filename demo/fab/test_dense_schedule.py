from dense_schedule import route, durations


def test_thousand_unique_steps_and_five_hundred_main_steps():
    steps = route()
    assert len(steps) == len({s['operation'] for s in steps}) == 1000
    assert sum(s['main_oper_yn'] == 'Y' for s in steps) == 500
    assert steps[-1]['operation'] == 'EPM'
    assert sum(s['skip_yn']=='Y' for s in steps)==50
    assert {'VIRTUAL','CNC'} <= {s['area_id'] for s in steps}
    assert {s['samp_seq'] for s in steps}=={0,1}
    assert all(s['skip_yn']=='N' for s in steps if s['operation'].startswith('oper ') or s['operation']=='EPM')


def test_realistic_durations_and_complete_route_total():
    totals = []
    for group in ('XXXA', 'XXXB', 'XXXC'):
        for n in range(1, 27):
            days, values = durations(f'{group}{n}')
            assert days == round(sum(values) / 24, 6)
            assert 30 <= days <= 60
            assert all(h==0 if s['skip_yn']=='Y' else 1/3 <= h <= 3 for s,h in zip(route(),values))
            assert durations(f'{group}{n}') == (days, values)
            totals.append(days)
    assert min(totals) == 30 and max(totals) == 60
