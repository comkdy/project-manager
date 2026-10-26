import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutKeyFlags,flagLabel} from '../src/ganttFlags.ts';

test('dense flags stay within the card, never overlap in a lane, and retain every operation',()=>{
 const items=Array.from({length:500},(_,i)=>({x:155+i*.5,label:`operation ${i}`,id:i}));
 const flags=layoutKeyFlags(items,155,405);
 assert.equal(flags.flatMap(f=>f.items).length,500);
 assert.deepEqual(flags.flatMap(f=>f.items).map(i=>i.id).sort((a,b)=>a-b),items.map(i=>i.id));
 for(const lane of [0,1]){
  const labels=flags.filter(f=>f.lane===lane);
  labels.forEach((f,i)=>{assert.ok(f.labelX>=155&&f.labelX+f.width<=405);if(i)assert.ok(f.labelX>=labels[i-1].labelX+labels[i-1].width+4)});
 }
 assert.ok(flags.some(f=>f.items.length>1));
});
test('sparse flags keep their own names; grouped labels disclose additional operations',()=>{
 const flags=layoutKeyFlags([{x:200,label:'oper A'},{x:360,label:'oper B'}],155,505);
 assert.equal(flags.length,2);assert.ok(flags.every(f=>f.items.length===1));
 assert.equal(flagLabel('oper A',1,100),'oper A');
 assert.equal(flagLabel('oper A CD',1,82),'oper A CD');
 assert.match(flagLabel('Long operation title',30,100),/… \+29$/);
 assert.deepEqual(layoutKeyFlags([],155,405),[]);
});
