import assert from 'node:assert/strict';
import test from 'node:test';
import {completionRange,insertCompletion} from '../src/sqlCompletion.ts';

test('completion replaces only the token at the caret and preserves the surrounding expression',()=>{
 const value='COALESCE(lot_i, 0) + value';
 const range=completionRange(value,14);
 assert.equal(range.prefix,'lot_i');
 assert.equal(insertCompletion(value,range,'lot_id').text,'COALESCE(lot_id, 0) + value');
});

test('quoted and selected identifiers are replaced without doubled quotes',()=>{
 const value='"lot_id" + 1';
 const range=completionRange(value,4);
 assert.equal(range.prefix,'lot');
 assert.equal(insertCompletion(value,range,'lot_code').text,'lot_code + 1');
 assert.equal(insertCompletion(value,completionRange(value,0,8),'value').text,'value + 1');
 assert.equal(insertCompletion(value,completionRange(value,8),'value').text,'value + 1');
 assert.equal(insertCompletion(value,completionRange(value,0),'value').text,'value + 1');
});

test('SQL strings and numeric constants never offer column completion',()=>{
 assert.equal(completionRange("CASE WHEN metric = 'lot' THEN 1 END",22),null);
 assert.equal(completionRange("'it''s lot",10),null);
 assert.equal(completionRange('value * 100',11),null);
 assert.deepEqual(completionRange('value + ',8),{start:8,end:8,prefix:''});
});

test('caret lands after the inserted identifier, including safely escaped quotes',()=>{
 const next=insertCompletion('val * 2',{start:0,end:3,prefix:'val'},'Mixed"Case');
 assert.equal(next.text,'"Mixed""Case" * 2');
 assert.equal(next.caret,13);
});
