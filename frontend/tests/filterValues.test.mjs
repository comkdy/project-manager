import assert from 'node:assert/strict';
import test from 'node:test';
import {changeFilterOperator,formatFilterValue,parseFilterValue} from '../src/filterValues.ts';

for(const op of ['in','not_in']){
test(`${op} sends a list of complete values, not a substring or comma-delimited scalar`,()=>{
 assert.deepEqual(parseFilterValue('LOT01, LOT02',op,'text'),{value:['LOT01','LOT02'],error:null});
 assert.deepEqual(parseFilterValue('1, 3, -4.25',op,'integer'),{value:[1,3,-4.25],error:null});
 assert.deepEqual(parseFilterValue('001, 002',op,'text').value,['001','002']);
 assert.deepEqual(parseFilterValue('',op,'text').value,[]);
});

test(`${op}: literal commas, quotes, whitespace and SQL-like strings survive editing`,()=>{
 const values=['A,B','a"b',"O'Brien",'  padded  ','',"x'); DROP TABLE wafers;--",'100%_'];
 const text=formatFilterValue(values,op);
 assert.deepEqual(parseFilterValue(text,op,'text'),{value:values,error:null});
});

test(`${op}: invalid and unfinished lists do not submit partial values or NaN`,()=>{
 for(const [text,type] of [['1,','integer'],['1, ,2','integer'],['1, abc','integer'],['"open','text'],['"closed"extra','text'],[Array(101).fill('x').join(','),'text']]){
  const result=parseFilterValue(text,op,type);
  assert.ok(result.error);
  assert.equal(result.value,text);
 }
 assert.ok(parseFilterValue('abc','eq','integer').error);
});
}

test('substring matching stays a string even for numeric columns',()=>{
 assert.deepEqual(parseFilterValue('12','contains','bigint'),{value:'12',error:null});
 assert.equal(parseFilterValue('1,2','contains','integer').value,'1,2');
});

test('operator changes adapt scalar/list shapes without treating a literal comma as two values',()=>{
 const original={column:'name',op:'eq',value:'A,B'};
 const membership=changeFilterOperator(original,'in','text');
 assert.deepEqual(membership.value,['A,B']);
 assert.equal(changeFilterOperator(membership,'contains','text').value,'A,B');
 assert.equal(changeFilterOperator({column:'id',op:'in',value:[12,34]},'contains','integer').value,'12');
});

test('large IDs and exact decimals do not get rounded by JavaScript',()=>{
 const values=['9007199254740993','0.1234567890123456789','1.0000000000000000001','1e-400'];
 assert.deepEqual(parseFilterValue(values.join(','),'in','numeric').value,values);
 assert.deepEqual(parseFilterValue('1, 2.5, 1e3','in','numeric').value,[1,2.5,1000]);
});

test('IN and NOT IN switching keeps the entire list, including literal commas',()=>{
 for(const [type,values] of [['text',['A,B',"O'Brien",'001']],['integer',[1,3,5]]]){
  const original={column:'value',op:'in',value:values};
  const exclusion=changeFilterOperator(original,'not_in',type);
  assert.deepEqual(exclusion.value,values);
  assert.deepEqual(changeFilterOperator(exclusion,'in',type),original);
  assert.equal(changeFilterOperator(exclusion,'eq',type).value,values[0]);
 }
});
