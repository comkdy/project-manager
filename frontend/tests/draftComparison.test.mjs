import assert from 'node:assert/strict';
import test from 'node:test';
import {describeDraft,compareDraftFields} from '../src/draftComparison.ts';
test('comparison presents nested knowledge inputs as named fields and hides bookkeeping',()=>{
 const input={proposal:{kind:'metric',scope:'database',content:{name:'수율',description:'설명\n두 번째 줄',details:{formula:'good / total',grain:'lot'},references:[{item_id:'k1',version:3}]}},note:'검토',targetRevision:7,dirty:true};
 const fields=describeDraft(input);
 assert.ok(fields.some(field=>field.label==='종류'&&field.text==='지표'));
 assert.ok(fields.some(field=>field.label==='계산식'&&field.text==='good / total'));
 assert.ok(fields.some(field=>field.label==='업무 설명'&&field.text==='설명\n두 번째 줄'));
 assert.ok(fields.some(field=>field.label.includes('연결한 승인 지식')&&field.label.includes('버전')&&field.text==='3'));
 assert.equal(fields.some(field=>/dirty|targetRevision|proposal|content/.test(field.label)),false);
});
test('only actual changed inputs are highlighted and multiline SQL and unfinished expressions stay exact',()=>{
 const rows=compareDraftFields({name:'동일',sql:'SELECT x\nFROM t',expressionDraft:' / ('},{name:'동일',sql:'SELECT y\nFROM t',expressionDraft:' / ('});
 assert.equal(rows.find(row=>row.label==='이름').changed,false);
 assert.equal(rows.find(row=>row.label==='조회 SQL').changed,true);
 assert.equal(rows.find(row=>row.label==='조회 SQL').mine,'SELECT x\nFROM t');
 assert.equal(rows.find(row=>row.label==='입력 중인 계산식').mine,' / (');
});
test('caller-provided field labels and unknown extension names remain readable without exposing object syntax',()=>{
 const fields=describeDraft([{label:'정의 내용',value:{custom_rule:'직접 입력한 규칙'}}]);
 assert.equal(fields[0].label,'정의 내용 · custom rule');assert.equal(fields[0].text,'직접 입력한 규칙');
});
