import assert from 'node:assert/strict';
import test from 'node:test';
import {aggregateChoices,cardIssue,changeDataset,changeKind,chooseValueSource,newCard,previewQueryCard,valueAggregates,valueSource} from '../src/cardEditing.ts';

const columns=[{name:'group',type:'text'},{name:'value',type:'numeric'},{name:'other',type:'integer'}];
const dataset={id:'data',name:'Data',version:1,columns,spec:{source_id:'s',columns:[],filters:[]}};
const card=()=>({...newCard(dataset),x:'group',y:'value',color:'group',aggregate:'avg'});

test('switching to histogram removes irrelevant fields and asks for a numeric X',()=>{
 const next=changeKind({...card(),options:{x_min:0,y_max:100,x_font_size:18}},'histogram',columns);
 assert.equal(next.x,undefined);assert.equal(next.y,undefined);assert.equal(next.color,undefined);
 assert.equal(next.aggregate,'count');assert.equal(next.options.y_max,undefined);assert.equal(next.options.x_font_size,18);
 assert.match(cardIssue(next,columns),/X축/);
 assert.equal(cardIssue({...next,x:'value'},columns),null);
});
test('KPI has no axes or group and cannot select an unaggregated measure',()=>{
 const next=changeKind({...card(),aggregate:'none'},'kpi',columns);
 assert.equal(next.x,undefined);assert.equal(next.y,undefined);assert.equal(next.color,undefined);
 assert.equal(next.aggregate,'count');assert.equal(cardIssue(next,columns),null);
 assert.ok(!aggregateChoices('kpi',columns).includes('none'));
 assert.ok(!aggregateChoices('bar',columns).includes('yield'));
});
test('dataset version changes preserve valid references and remove missing filters and ranges',()=>{
 const next=changeDataset({...card(),filters:[{column:'group',op:'eq',value:'A'},{column:'gone',op:'eq',value:1}],options:{x_min:0,y_max:100}}, {...dataset,version:2,columns:columns.filter(c=>c.name!=='value')});
 assert.equal(next.dataset_version,2);assert.equal(next.x,'group');assert.equal(next.y,undefined);
 assert.equal(next.filters.length,1);assert.equal(next.options.x_min,undefined);
 assert.match(cardIssue(next,dataset.columns),/Y축/);
});
test('formatting does not change the data query; grouping, filters and bins do',()=>{
 const before=card();
 assert.deepEqual(previewQueryCard(before),previewQueryCard({...before,renderer:'seaborn'}));
 assert.deepEqual(previewQueryCard(before),previewQueryCard({...before,title:'Renamed',options:{...before.options,palette:'warm',x_font_size:22,y_min:0}}));
 for(const patch of [{color:'other'},{filters:[{column:'other',op:'gt',value:2}]},{options:{bins:50}}])assert.notDeepEqual(previewQueryCard(before),previewQueryCard({...before,...patch}));
});
test('invalid ranges and stale columns stop preview until corrected',()=>{
 assert.match(cardIssue({...card(),options:{y_min:10,y_max:5}},columns),/최솟값/);
 assert.match(cardIssue({...card(),y:'deleted'},columns),/컬럼이 없습니다/);
 assert.equal(cardIssue(card(),columns),null);
});

test('saved automatic ranges are null and must not block the initial preview',()=>{
 assert.equal(cardIssue({...card(),options:{x_min:null,x_max:null,y_min:null,y_max:null}},columns),null);
 assert.equal(cardIssue({...card(),options:{y_min:null,y_max:-5}},columns),null);
 assert.equal(cardIssue({...card(),options:{y_min:5,y_max:null}},columns),null);
 assert.match(cardIssue({...card(),options:{y_min:0,y_max:0}},columns),/최솟값/);
});

test('data sources describe actual row count and yield inputs even with a stale Y',()=>{
 assert.equal(valueSource({...card(),aggregate:'count'}).label,'전체 행');
 assert.equal(valueSource({...card(),aggregate:'yield'}).label,'passed_dies / tested_dies');
 assert.equal(valueSource(card()).label,'value');
 for(const kind of ['kpi','bar','line']){
  const next=chooseValueSource({...card(),kind,aggregate:'count'},'column:other');
  assert.equal(next.y,'other');assert.equal(next.aggregate,'avg');assert.equal(next.x,'group');
  assert.equal(valueSource(next).label,'other');
  assert.ok(valueAggregates(next,columns).includes('sum'));
  assert.equal(chooseValueSource(next,'yield').y,undefined);
 }
});

test('wafer cell data is independent of X and Y and supports legacy inspection yield',()=>{
 const wafer={...card(),kind:'wafer',x:'other',y:'value',color:undefined,aggregate:'none'};
 const inspection=[...columns,{name:'passed',type:'boolean'},{name:'status',type:'text'}];
 assert.equal(valueSource(wafer,inspection).selection,'wafer-yield');
 assert.equal(valueSource(wafer,columns).selection,'rows');
 const next=chooseValueSource(wafer,'column:other');
 assert.equal(next.x,'other');assert.equal(next.y,'value');assert.equal(next.color,'other');
 assert.equal(next.aggregate,'avg');assert.equal(cardIssue(next,inspection),null);
 assert.equal(valueSource({...next,aggregate:'none'},inspection).detail,'평균');
 assert.match(cardIssue({...next,color:'group'},inspection),/숫자형/);
 assert.match(cardIssue({...next,color:undefined},inspection),/셀 값/);
 assert.deepEqual(chooseValueSource(next,'wafer-yield'),wafer);
 assert.equal(chooseValueSource(next,'rows').y,'value');
 assert.equal(chooseValueSource(next,'rows').color,undefined);
});
