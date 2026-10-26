import assert from 'node:assert/strict';
import test from 'node:test';
import {recentFourWeeks,isoWeek,weekMonday,weeksInYear,validWeek} from '../src/dashboardPeriod.ts';

test('four weeks includes the current ISO week and previous three, in Korea time',()=>{
 assert.deepEqual(recentFourWeeks(new Date('2026-10-21T01:00:00Z')),{year:2026,week:40});
 assert.deepEqual(recentFourWeeks(new Date('2026-10-18T15:00:00Z')),{year:2026,week:40});
 assert.deepEqual(recentFourWeeks(new Date('2026-10-18T14:59:59Z')),{year:2026,week:39});
});
test('ISO weeks handle year rollover and 53-week years',()=>{
 assert.deepEqual(isoWeek(new Date('2021-01-01Z')),{year:2020,week:53});
 assert.equal(weekMonday({year:2020,week:53}),'2020-12-28');
 assert.equal(weekMonday({year:2027,week:1}),'2027-01-04');
 assert.equal(weeksInYear(2026),53);assert.equal(weeksInYear(2025),52);
 assert.equal(validWeek({year:2025,week:53}),false);assert.equal(validWeek({year:2026,week:53}),true);
});
