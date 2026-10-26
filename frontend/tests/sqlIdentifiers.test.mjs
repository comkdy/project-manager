import assert from 'node:assert/strict';
import test from 'node:test';
import {columnExpression,displayExpression} from '../src/sqlIdentifiers.ts';

test('ordinary column names are bare while case-sensitive and reserved names stay quoted',()=>{
 for(const name of ['lot_id','value','equipment__name'])assert.equal(columnExpression(name),name);
 for(const name of ['MixedCase','select','current_date','lot id'])assert.equal(columnExpression(name),`"${name}"`);
 assert.equal(columnExpression('a"b'),'"a""b"');
});

test('display removes redundant identifier quotes inside expressions, preserving SQL literals',()=>{
 assert.equal(displayExpression('COALESCE("lot_id", 0) + "value"'),'COALESCE(lot_id, 0) + value');
 const expression=`CASE WHEN "lot_code" = 'it''s "lot_id"' THEN "MixedCase" ELSE "value" END`;
 assert.equal(displayExpression(expression),`CASE WHEN lot_code = 'it''s "lot_id"' THEN "MixedCase" ELSE value END`);
 for(const literal of [String.raw`E'it\'s "lot_id"'`,'$$"lot_id"$$','$tag$"lot_id"$tag$']){
  assert.equal(displayExpression(`${literal} || "lot_code"`),`${literal} || lot_code`);
 }
});
