import test from 'node:test';
import assert from 'node:assert/strict';
import {operationIntervals,ribbonSegments} from '../src/ganttIntervals.ts';
const at=h=>`2026-10-22T${String(h).padStart(2,'0')}:00:00Z`;
const row=(start,end,status='completed',expected=null)=>({start_at:start===null?null:at(start),end_at:end===null?null:at(end),status,current_expected_end:expected===null?null:at(expected)});
const intervals=(route,asOf=12)=>operationIntervals(route,Date.parse(at(asOf)));

test('major boxes include hidden time and meet the next major start without changing source timings',()=>{
 const route=[row(1,2),row(5,6),row(8,9)],before=structuredClone(route),result=intervals(route);
 assert.deepEqual(result.map(r=>r.end),[Date.parse(at(5)),Date.parse(at(8)),Date.parse(at(9))]);
 assert.deepEqual(result.map(r=>r.hours),[4,3,1]);
 assert.equal(result[0].end,result[1].start);
 assert.equal(result[1].end,result[2].start);
 assert.equal(result[2].extended,false); // Last major retains its own known end.
 assert.deepEqual(route,before);
});
test('unknown or overlapping times do not fabricate, bridge over an unknown major, or shorten a box',()=>{
 assert.equal(intervals([row(1,3),row(2,4)])[0].end,Date.parse(at(3)));
 assert.equal(intervals([row(1,2),row(null,null),row(6,7)])[0].end,Date.parse(at(2)));
 assert.equal(intervals([row(1,null),row(6,7)])[0].end,null);
 assert.equal(intervals([row(null,2),row(6,7)])[0].extended,false);
 assert.equal(intervals([{...row(1,2),end_at:'invalid'},row(6,7)])[0].end,null);
});
test('current and future major boxes include forecast tails once and retain their interval status',()=>{
 const result=intervals([row(1,2),row(5,6,'upcoming'),row(8,9,'upcoming')],3);
 assert.equal(result[0].status,'current'); // Hidden step is in progress after this completed major.
 assert.equal(result[1].status,'upcoming');
 assert.equal(intervals([row(1,2),row(5,6)],5)[0].status,'completed');
 const current=intervals([row(1,2,'current',5),row(7,8,'upcoming')],2)[0];
 assert.equal(current.end,Date.parse(at(7)));
 assert.equal(current.hours,6);
 assert.equal(current.status,'current');
 assert.equal(intervals([row(1,2,'current',5)],2)[0].end,Date.parse(at(5)));
});

test('last major includes the hidden tail through the full plan end, without shifting earlier boxes',()=>{
 const route=[row(1,2),row(5,6,'upcoming')],before=structuredClone(route);
 const result=operationIntervals(route,Date.parse(at(3)),at(10));
 assert.equal(result[0].end,Date.parse(at(5)));
 assert.equal(result[0].toPlanEnd,false);
 assert.equal(result[1].end,Date.parse(at(10)));
 assert.equal(result[1].hours,5);
 assert.equal(result[1].toPlanEnd,true);
 assert.equal(result[1].next,null);
 assert.deepEqual(route,before);
 for(const unknown of [null,'invalid',at(4)])
  assert.equal(operationIntervals(route,Date.parse(at(3)),unknown)[1].end,Date.parse(at(6)));
});

test('ribbon merges adjacent fills while preserving status changes, missing time and the full plan end',()=>{
 const data=operationIntervals([row(1,2),row(3,4),row(5,6,'upcoming'),row(7,8,'upcoming')],Date.parse(at(5)),at(10));
 const before=structuredClone(data),bands=ribbonSegments(data);
 assert.deepEqual(bands,[{start:Date.parse(at(1)),end:Date.parse(at(5)),status:'completed'},
  {start:Date.parse(at(5)),end:Date.parse(at(10)),status:'upcoming'}]);
 assert.deepEqual(data,before);
 assert.equal(ribbonSegments([{start:1,end:2,status:'upcoming'},
  {start:null,end:null,status:'upcoming'},{start:4,end:5,status:'upcoming'}]).length,2);
});
