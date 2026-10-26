import test from 'node:test';
import assert from 'node:assert/strict';
import {initialPivot,pivotOutputs,pivotIssue} from '../src/pivotSettings.ts';
import {editorRequest,sqlSnapshot} from '../src/sqlDraft.ts';
const source={id:'m',primary_key:'id',columns:[['id','bigint'],['lot_code','text'],['wf_id','integer'],['die_x','integer'],['die_y','integer'],['operation','text'],['value','numeric'],['measured_at','timestamp with time zone']].map(([name,type])=>({name,type}))};
const spec={source_id:'m',columns:[],filters:[{column:'lot_code',op:'eq',value:'L1'}]};
function pivot(){const p=initialPivot(source,spec);return {...p,outputs:pivotOutputs(['A','B'],p)}}

test('suggests existing die keys and measurement columns without inventing schema',()=>{
 const p=pivot();assert.deepEqual(p.keys,['lot_code','wf_id','die_x','die_y']);assert.equal(p.time_column,'measured_at');assert.equal(p.tie_breaker,'id');assert.equal(pivotIssue(p,source),null);
 assert.deepEqual(p.filters,spec.filters);p.filters[0].value='changed';assert.equal(spec.filters[0].value,'L1');
 const unknown=initialPivot({id:'other',primary_key:'',columns:[{name:'timestamp_1',type:'timestamp'},{name:'timestamp_2',type:'timestamp'}]},spec);
 assert.deepEqual(unknown.keys,[]);assert.equal(unknown.time_column,'');assert.equal(unknown.operation_column,'');
});
test('output aliases remain stable on reordering, avoid collisions and fit PostgreSQL byte limits',()=>{
 const p=pivot();p.outputs[0].name='custom_x';
 const outputs=pivotOutputs(['B','A','a','A!','A?','한'.repeat(100)],p);
 assert.deepEqual(outputs.slice(0,2),[p.outputs[1],p.outputs[0]]);
 assert.equal(new Set(outputs.map(c=>c.name)).size,outputs.length);
 assert.ok(outputs.every(c=>new TextEncoder().encode(c.name).length<=63));
 assert.equal(outputs[0].name,'value_b');
});
test('blocks incomplete, stale, conflicting and unsupported column settings',()=>{
 assert.match(pivotIssue({...pivot(),tie_breaker:''},source),/고유 번호/);
 assert.match(pivotIssue({...pivot(),value_column:'operation'},source),/서로 다른/);
 assert.match(pivotIssue({...pivot(),keys:['gone']},source),/삭제된/);
 assert.match(pivotIssue({...pivot(),outputs:[{value:'A',name:'lot_code'}]},source),/중복/);
 assert.match(pivotIssue({...pivot(),outputs:[]},source),/열 항목/);
});
test('generated SQL keeps its recipe; directly edited SQL executes as typed without stale pivot settings',()=>{
 const form={source_id:'m',columns:[],filters:[],pivot:pivot()};
 const sql='WITH generated AS (...) SELECT * FROM generated';
 const snapshot=sqlSnapshot(form,sql);
 const untouched=editorRequest('sql',spec,sql,snapshot);
 assert.deepEqual(untouched.spec.pivot,form.pivot);assert.equal(untouched.spec.raw_sql,sql);
 const edited=editorRequest('sql',spec,sql+' -- edited',snapshot);
 assert.equal(edited.spec.raw_sql,sql+' -- edited');assert.equal(edited.spec.pivot,undefined);
 const changed=editorRequest('form',{...form,pivot:{...form.pivot,require_all:false}},sql,snapshot);
 assert.equal(changed.editor_mode,'simple');assert.equal(changed.spec.raw_sql,null);assert.equal(changed.spec.pivot.require_all,false);
});
