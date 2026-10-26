import assert from 'node:assert/strict';
import test from 'node:test';
import {supportsLogAxis,changeAxisScale,normalizeAxisScales,plotlyAxisScale,logScaleWarning} from '../src/cardScales.ts';
import {cardIssue,changeKind,changeDataset,previewQueryCard} from '../src/cardEditing.ts';

const columns=[{name:'value',type:'numeric'},{name:'group',type:'text'},{name:'time',type:'timestamp'}];
const base={id:'log',title:'Scale',dataset_id:'data',dataset_version:1,kind:'scatter',renderer:'plotly',x:'value',y:'value',aggregate:'none',filters:[],options:{}};

test('log options are limited to quantitative axes in supported charts',()=>{
 for(const kind of ['scatter','line','histogram','bar','box','wafer','kpi']){
  const card={...base,kind};
  assert.equal(supportsLogAxis(card,'x',columns),['scatter','line','histogram'].includes(kind),kind);
  assert.equal(supportsLogAxis(card,'y',columns),['scatter','line','histogram','bar','box'].includes(kind),kind);
 }
 for(const x of ['group','time',undefined])assert.equal(supportsLogAxis({...base,x},'x',columns),false);
 assert.equal(supportsLogAxis({...base,kind:'line',y:'group',aggregate:'min'},'y',columns),false);
 for(const aggregate of ['count','yield'])assert.equal(supportsLogAxis({...base,kind:'bar',y:undefined,aggregate},'y',columns),true);
 assert.equal(supportsLogAxis({...base,y:undefined,value_expression:[{kind:'column',name:'value'}]},'y',columns),true);
});

test('manual bounds stay real values in the card and become exponents only for Plotly',()=>{
 const card={...base,options:{x_scale:'log',x_min:.1,x_max:1000,y_min:-2,y_max:10}};
 assert.deepEqual(plotlyAxisScale(card,'x'),{type:'log',range:[-1,3]});
 assert.deepEqual(plotlyAxisScale(card,'y'),{type:'-',range:[-2,10]});
 assert.deepEqual(plotlyAxisScale({...card,options:{x_scale:'log',x_min:10}},'x'),{type:'log',range:[1,null]});
 assert.deepEqual(plotlyAxisScale({...card,options:{x_scale:'log'}},'x'),{type:'log',range:undefined});
 assert.equal(card.options.x_min,.1);
});

test('switching to log clears incompatible linear bounds without mutating the committed card',()=>{
 const original={...base,options:{x_min:0,x_max:100,y_min:-10}};
 const draft=changeAxisScale(original,'x','log');
 assert.equal(original.options.x_scale,undefined);assert.equal(original.options.x_min,0);
 assert.equal(draft.options.x_min,undefined);assert.equal(draft.options.x_max,100);
 assert.equal(draft.options.y_min,-10);
 assert.equal(cardIssue({...draft,options:{...draft.options,x_min:0}},columns),'X축 로그 범위에는 0보다 큰 값을 입력하세요.');
 assert.match(cardIssue({...draft,options:{...draft.options,x_min:Infinity}},columns),/0보다 큰 값/);
 assert.equal(cardIssue(draft,columns),null);
});

test('changing kind or dataset removes incompatible scales and formatting reuses query rows',()=>{
 const card={...base,options:{x_scale:'log',y_scale:'log'}};
 assert.equal(changeKind(card,'bar',columns).options.x_scale,'linear');
 assert.equal(changeKind(card,'bar',columns).options.y_scale,'log');
 assert.equal(changeKind(card,'wafer',columns).options.y_scale,'linear');
 const changed=changeDataset(card,{id:'data',version:2,columns:[{name:'value',type:'text'}]});
 assert.equal(changed.options.x_scale,'linear');assert.equal(changed.options.y_scale,'linear');
 const category=normalizeAxisScales({...card,x:'group',options:{...card.options,x_min:1,x_max:1000}},columns);
 assert.equal(category.options.x_scale,'linear');assert.equal(category.options.x_min,undefined);assert.equal(category.options.x_max,undefined);
 assert.deepEqual(previewQueryCard(base),previewQueryCard(card));
});

test('nonpositive data produces a clear warning without altering query results',()=>{
 const card={...base,options:{x_scale:'log',y_scale:'log'}};
 const rows=[{x:-1,y:10},{x:0,y:100},{x:1,y:1000}];
 assert.match(logScaleWarning(card,rows),/X축의 0 이하/);
 assert.match(logScaleWarning(card,[{x:0,y:1},{x:-5,y:2}]),/표시할 수 없습니다/);
 assert.equal(logScaleWarning(card,[{x:1,y:10},{x:100,y:1000}]),null);
 assert.equal(logScaleWarning(base,rows),null);
 assert.equal(rows[0].x,-1);
});
