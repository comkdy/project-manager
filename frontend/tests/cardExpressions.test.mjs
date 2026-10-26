import assert from 'node:assert/strict';
import test from 'node:test';
import {allowedDraft,columnSuggestions,editableValueCard,expressionIssue,expressionName,expressionText,isPercent,parseExpressionText,yieldExpression} from '../src/cardExpressions.ts';
import {cardIssue,changeDataset,changeKind,chooseValueSource,previewQueryCard,valueSource} from '../src/cardEditing.ts';

const columns=[{name:'passed_dies',type:'bigint'},{name:'tested_dies',type:'bigint'},{name:'a',type:'numeric'},{name:'b',type:'real'},{name:'한글 값',type:'integer'},{name:'odd]name',type:'integer'},{name:'label',type:'text'}];
const base={id:'test',title:'Yield',dataset_id:'data',dataset_version:1,kind:'kpi',aggregate:'yield',filters:[],options:{}};
const parse=text=>parseExpressionText(text,columns);

test('existing yield becomes editable chips without changing sum-before-division semantics or percent display',()=>{
 const card=editableValueCard(base);
 assert.deepEqual(card.value_expression,yieldExpression());
 assert.equal(card.aggregate,'sum');assert.ok(isPercent(card));
 assert.equal(valueSource(card).selection,'expression');
 assert.equal(cardIssue(card,columns),null);
 assert.equal(base.value_expression,undefined);
 assert.equal(card.value_expression_name,'수율');
});

test('arithmetic, decimal numbers, parentheses, unary minus and quoted column names round trip as typed tokens',()=>{
 for(const text of ['passed_dies / tested_dies * 100','(a - b) / -2.5','[한글 값] + .5','[odd]]name] * a']){
  const tokens=parse(text);
  assert.equal(expressionIssue(tokens,columns),null);
  assert.deepEqual(parse(expressionText(tokens)),tokens);
 }
});

test('unsupported pasted operators, functions, SQL, unknown names and malformed sequences are rejected atomically',()=>{
 for(const text of ['a % b','a ** 2','SUM(a)','SELECT a','a; DROP TABLE cells','a -- comment','a // b','a /* test */ + b','a = b','a < b','a ?? 0','missing + 1','label + 1','a b','a + * b','a )','1e6 + a','NaN + a','a + <script>','a + "anything"'])assert.throws(()=>parse(text),undefined,text);
});

test('pending input only accepts numeric text or fragments of known numeric columns',()=>{
 for(const text of ['pass','dies','한글','1','1.','-.','@','random garbage','alert(1)']){
  assert.equal(allowedDraft(text,columns),['pass','dies','한글','1','1.'].includes(text),text);
 }
 assert.deepEqual(columnSuggestions('pass',columns).map(c=>c.name),['passed_dies']);
 assert.ok(!columnSuggestions('',columns).some(c=>c.name==='label'));
});

test('single-letter completion prioritizes column prefixes and supports replacing an existing chip',()=>{
 const available=[{name:'sample',type:'numeric'},{name:'project_id',type:'integer'},...columns,{name:'plain_text',type:'text'}];
 assert.deepEqual(columnSuggestions('p',available).map(c=>c.name),['project_id','passed_dies','sample']);
 assert.deepEqual(columnSuggestions('P',available),columnSuggestions('p',available));
 const tokens=parse('passed_dies / tested_dies * 100');
 assert.equal(expressionIssue([{kind:'column',name:'a'},...tokens.slice(1)],columns),null);
 assert.ok(expressionIssue([...tokens,{kind:'column',name:'a'}],columns));
});

test('unfinished expressions can be edited but cannot preview or save',()=>{
 for(const text of ['a +','(a + b','-']){
  const tokens=parse(text);
  assert.equal(expressionIssue(tokens,columns,true),null);
  assert.ok(expressionIssue(tokens,columns));
  assert.ok(cardIssue({...editableValueCard(base),value_expression:tokens},columns));
 }
 assert.ok(expressionIssue(parse('100'),columns));
 assert.ok(expressionIssue(Array.from({length:101},()=>({kind:'operator',value:'('})),columns));
});

test('changing the source clears the expression while dataset changes keep missing references visible',()=>{
 const card=editableValueCard(base),selected=chooseValueSource(card,'column:a');
 assert.equal(selected.value_expression,undefined);assert.equal(selected.y,'a');assert.equal(isPercent(selected),false);
 assert.equal(selected.value_expression_name,undefined);
 assert.equal(chooseValueSource(card,'rows').value_expression,undefined);
 assert.equal(changeKind(card,'wafer',columns).aggregate,'sum');
 assert.equal(chooseValueSource({...base,kind:'bar',aggregate:'none',y:'a'},'expression').aggregate,'none');
 const changed=changeDataset(card,{id:'other',version:2,columns:[columns[2]]});
 assert.match(cardIssue(changed,[columns[2]]),/passed_dies/);
 assert.equal(changeKind(card,'histogram',columns).value_expression,undefined);
 assert.equal(changeKind(card,'histogram',columns).value_expression_name,undefined);
 const scatter=changeKind(card,'scatter',columns);
 assert.equal(scatter.aggregate,'none');assert.deepEqual(scatter.value_expression,card.value_expression);
});

test('expression names replace long formulas without changing query or editable tokens',()=>{
 const card=editableValueCard(base),renamed={...card,value_expression_name:'  최종 수율  '};
 assert.equal(valueSource(renamed).label,'최종 수율');
 assert.deepEqual(previewQueryCard(card),previewQueryCard(renamed));
 const reopened=editableValueCard(JSON.parse(JSON.stringify(renamed)));
 assert.deepEqual(reopened.value_expression,yieldExpression());
 assert.equal(expressionName(reopened),'최종 수율');
 assert.equal(expressionName({...card,value_expression_name:'   '}),'직접 계산식');
 assert.equal(expressionName({...card,value_expression_name:undefined}),'직접 계산식');
 for(const kind of ['bar','line','scatter','box','wafer'])assert.equal(valueSource(changeKind(renamed,kind,columns)).label,'최종 수율');
});

test('wafer expression uses a separate cell value and preserves coordinates',()=>{
 const wafer={...base,kind:'wafer',x:'a',y:'b',color:'a',aggregate:'avg'};
 const custom=chooseValueSource(wafer,'expression');
 assert.deepEqual(custom.value_expression,[{kind:'column',name:'a'}]);
 assert.equal(custom.color,undefined);assert.equal(custom.x,'a');assert.equal(custom.y,'b');
 assert.equal(cardIssue(custom,columns),null);
});
