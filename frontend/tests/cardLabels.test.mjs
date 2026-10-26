import assert from 'node:assert/strict';
import test from 'node:test';
import {axisLabel} from '../src/cardLabels.ts';
import {changeDataset,changeKind,chooseValueSource,previewQueryCard} from '../src/cardEditing.ts';
import {yieldExpression} from '../src/cardExpressions.ts';

const columns=[{name:'lot_code',type:'text'},{name:'value',type:'numeric'},{name:'other',type:'numeric'}];
const card={id:'labels',title:'Chart',dataset_id:'data',dataset_version:1,kind:'bar',renderer:'plotly',x:'lot_code',y:'value',aggregate:'avg',filters:[],options:{}};

test('empty or whitespace labels fall back to the original column name',()=>{
 for(const label of [undefined,null,'','   ']){
  const c={...card,options:{x_label:label,y_label:label}};
  assert.equal(axisLabel(c,'x'),'lot_code');assert.equal(axisLabel(c,'y'),'value');
 }
});

test('display labels never change column identifiers or the preview data query',()=>{
 const labeled={...card,options:{x_label:' 랏코드 ',y_label:'측정값'}};
 assert.equal(axisLabel(labeled,'x'),'랏코드');assert.equal(axisLabel(labeled,'y'),'측정값');
 assert.equal(labeled.x,'lot_code');assert.equal(labeled.y,'value');
 assert.deepEqual(previewQueryCard(labeled),previewQueryCard(card));
 for(const kind of ['bar','line','scatter','box','wafer']){
  assert.equal(axisLabel({...labeled,kind},'x'),'랏코드');
  assert.equal(axisLabel({...labeled,kind},'y'),'측정값');
 }
});

test('expression names and automatic measures are separate from column labels',()=>{
 const labeled={...card,options:{y_label:'이전 컬럼 이름'}};
 assert.equal(axisLabel({...labeled,value_expression:yieldExpression(),value_expression_name:'수율'},'y'),'수율');
 assert.equal(axisLabel({...labeled,kind:'histogram'},'y'),'개수');
 assert.equal(axisLabel({...labeled,aggregate:'count'},'y'),'개수');
});

test('changing the selected column clears only its obsolete display name',()=>{
 const labeled={...card,options:{x_label:'랏코드',y_label:'측정값'}};
 assert.equal(chooseValueSource(labeled,'column:other').options.y_label,undefined);
 assert.equal(chooseValueSource(labeled,'column:value').options.y_label,'측정값');
 const next=changeDataset(labeled,{id:'new',version:1,columns:columns.filter(c=>c.name!=='value')});
 assert.equal(next.options.y_label,undefined);assert.equal(next.options.x_label,'랏코드');
 assert.equal(changeKind(labeled,'histogram',columns).options.x_label,undefined);
});
