import assert from 'node:assert/strict';
import test from 'node:test';
import {sortTableRows} from '../src/tableRows.ts';

test('sorts only the loaded 100 rows and restores their exact original order',()=>{
 const rows=Object.freeze(Array.from({length:100},(_,id)=>Object.freeze({id,value:(id*17)%100})));
 const columns=[{name:'value',type:'integer'}];
 const asc=sortTableRows(rows,columns,'value','asc');
 const desc=sortTableRows(rows,columns,'value','desc');
 assert.deepEqual(asc.map(r=>r.value),Array.from({length:100},(_,i)=>i));
 assert.deepEqual(desc.map(r=>r.value),Array.from({length:100},(_,i)=>99-i));
 assert.deepEqual(new Set(asc),new Set(rows));
 assert.deepEqual(new Set(desc),new Set(rows));
 assert.equal(sortTableRows(rows,columns,'','asc'),rows);
 assert.equal(sortTableRows(rows,columns,'missing','desc'),rows);
});

test('keeps NULL values last and equal values in their loaded order in both directions',()=>{
 const rows=[{id:1,value:null},{id:2,value:2},{id:3,value:-10},{id:4,value:2},{id:5},{id:6,value:0}];
 const columns=[{name:'value',type:'numeric'}];
 assert.deepEqual(sortTableRows(rows,columns,'value','asc').map(r=>r.id),[3,6,2,4,1,5]);
 assert.deepEqual(sortTableRows(rows,columns,'value','desc').map(r=>r.id),[2,4,6,3,1,5]);
});

test('sorts decimal strings numerically and timestamps by their actual instant',()=>{
 const values=[{value:'10.5'},{value:'-2.1'},{value:'2.25'}];
 assert.deepEqual(sortTableRows(values,[{name:'value',type:'numeric(12, 3)'}],'value','asc').map(r=>r.value),['-2.1','2.25','10.5']);
 const dates=[{id:1,value:'2026-10-20T09:30:00+09:00'},{id:2,value:'2026-10-20T01:00:00Z'},{id:3,value:'2026-10-19T23:00:00Z'}];
 assert.deepEqual(sortTableRows(dates,[{name:'value',type:'timestamp with time zone'}],'value','asc').map(r=>r.id),[3,1,2]);
});

test('supports natural text and boolean ordering',()=>{
 const text=[{value:'Lot 10'},{value:'Lot 2'},{value:'Lot 1'}];
 assert.deepEqual(sortTableRows(text,[{name:'value',type:'text'}],'value','asc').map(r=>r.value),['Lot 1','Lot 2','Lot 10']);
 const flags=[{value:true},{value:false},{value:null}];
 assert.deepEqual(sortTableRows(flags,[{name:'value',type:'boolean'}],'value','asc').map(r=>r.value),[false,true,null]);
 assert.deepEqual(sortTableRows(flags,[{name:'value',type:'boolean'}],'value','desc').map(r=>r.value),[true,false,null]);
});
