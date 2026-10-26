"""Pure generator checks; no PostgreSQL needed."""
import unittest
from seed import DIE_SITES, PROFILES, inline_row, die_row, wafer_row, rows_for


class GeneratorTests(unittest.TestCase):
    def test_profile_sizes(self):
        dev_lots,dev_sites=PROFILES['dev']; perf_lots,perf_sites=PROFILES['perf']
        self.assertLessEqual(dev_lots*25*(dev_sites+18+len(DIE_SITES)),150000)
        self.assertGreaterEqual(perf_lots*25*perf_sites,2000000)
        self.assertGreaterEqual(perf_lots*25*len(DIE_SITES),1000000)

    def test_id_determinism_and_subset(self):
        expected={row[0]:row for row in rows_for('inline_measurements',42,1,100)}
        actual={row[0]:row for row in rows_for('inline_measurements',42,1,1400) if row[0] in expected}
        self.assertEqual(expected,actual)
        self.assertNotEqual(inline_row(42,wafer_row(1),0),inline_row(43,wafer_row(1),0))

    def test_die_status_and_anomaly(self):
        failure_rates=[]
        for wafer_id in (1,76):
            rows=[die_row(20261019,wafer_row(wafer_id),i) for i in range(len(DIE_SITES))]
            tested=[r for r in rows if r[-3]=='tested']
            failure_rates.append(sum(r[-2] is False for r in tested)/len(tested))
            for row in rows:
                self.assertEqual(row[-2] is None,row[-3]=='untested')
                self.assertEqual(row[-1] is None,row[-3]=='untested')
        self.assertGreater(failure_rates[1]-failure_rates[0],0.1)


if __name__=='__main__': unittest.main()
