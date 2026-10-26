import assert from 'node:assert/strict';
import test from 'node:test';
import {editorRequest,sqlSnapshot} from '../src/sqlDraft.ts';

const spec={source_id:'inline',columns:[{name:'lot_code',expression:'lot_code'}],filters:[],group_by:[],raw_sql:null};
const sql='-- keep my alias and formatting\nselect "m".lot_code from public.inline_measurements AS "m"\nwhere \'a:b\' = \'a:b\';';

test('SQL execution sends the text exactly as typed',()=>{
 const request=editorRequest('sql',spec,sql,null);
 assert.equal(request.editor_mode,'sql');
 assert.equal(request.spec.raw_sql,sql);
 assert.deepEqual(request.spec.columns,[]);
 assert.deepEqual(request.spec.filters,[]);
});

test('switching through the simple editor without edits preserves SQL text',()=>{
 const snapshot=sqlSnapshot(spec,sql);
 const request=editorRequest('form',structuredClone(spec),sql,snapshot);
 assert.equal(request.editor_mode,'sql');
 assert.equal(request.spec.raw_sql,sql);
});

test('changing a form expression or filter generates new SQL, undoing restores the original',()=>{
 const snapshot=sqlSnapshot(spec,sql);
 for(const changed of [
  {...spec,columns:[{name:'normalized',expression:'LOWER(lot_code)'}]},
  {...spec,filters:[{column:'lot_code',op:'eq',value:'LOT1'}]},
 ]){
  const request=editorRequest('form',changed,sql,snapshot);
  assert.equal(request.editor_mode,'simple');
  assert.deepEqual(request.spec,changed);
 }
 assert.equal(editorRequest('form',spec,sql,snapshot).spec.raw_sql,sql);
});
