import assert from 'node:assert/strict';
import test from 'node:test';
import {datasetSpec,editorSpec,outputName,sourceColumn} from '../src/datasetColumns.ts';

const inputs=['value','wafer_id','MixedCase'].map(name=>({name,type:'integer'}));
const catalog=[{id:'inline',columns:inputs},{id:'equipment',columns:[{name:'name',type:'text'}]}];
const spec=columns=>({source_id:'inline',columns,filters:[],joins:[]});

test('default input references remain visible and expressions are required',()=>{
 const draft=editorSpec(spec([{name:'value',expression:'"value"'}]),catalog);
 assert.deepEqual(draft.columns,[{name:'',expression:'value'}]);
 assert.equal(outputName(draft.columns[0]),'value');
 draft.columns[0].name='renamed_value';
 assert.deepEqual(datasetSpec(draft).columns,[{name:'renamed_value',expression:'value'}]);
 draft.columns[0].expression='   ';
 assert.throws(()=>datasetSpec(draft),/SQL 표현식/);
 assert.equal(sourceColumn({name:'misleading_alias',expression:'"value"'},inputs),'value');
 assert.equal(sourceColumn({name:'value',expression:'value * 2'},inputs),undefined);
});

test('explicit aliases and joined fields survive editing and normalization',()=>{
 const columns=[{name:'wafer',expression:'"wafer_id"'},{name:'tool',expression:'"equipment__name"'},{name:'mixed',expression:'"MixedCase"'},{name:'value',expression:'value * 2'}];
 const draft=editorSpec(spec(columns),catalog);
 assert.deepEqual(datasetSpec(draft).columns,[{name:'wafer',expression:'wafer_id'},{name:'tool',expression:'equipment__name'},{name:'mixed',expression:'"MixedCase"'},{name:'value',expression:'value * 2'}]);
 assert.ok(draft.columns.every(c=>!!c.expression));
});

test('readable expressions preserve saved output labels and filter references',()=>{
 const expression='"value" * 1.5';
 const initial={...spec([{name:expression,expression}]),filters:[{column:expression,op:'gt',value:1}]};
 const draft=editorSpec(initial,catalog);
 assert.deepEqual(draft.columns,[{name:expression,expression:'value * 1.5'}]);
 assert.equal(datasetSpec(draft).filters[0].column,draft.columns[0].name);
});

test('an omitted output name follows the expression, including changed references',()=>{
 const column={name:'',expression:'"wafer_id"'};
 assert.equal(outputName(column),'wafer_id');
 column.expression='"value"';
 assert.equal(outputName(column),'value');
 for(const expression of ['value * 1.5','AVG(value)','COUNT(*)','TRUE','1']){
  assert.deepEqual(datasetSpec(spec([{name:'',expression}])).columns,[{name:expression,expression}]);
 }
});

test('ambiguous and oversized default names require a short explicit alias',()=>{
 assert.throws(()=>datasetSpec(spec([{name:'',expression:'value'},{name:'value',expression:'value * 2'}])),/중복/);
 const expression='COALESCE(value, 0) + '.repeat(4)+'1';
 assert.throws(()=>datasetSpec(spec([{name:'',expression}])),/출력 이름/);
 assert.equal(datasetSpec(spec([{name:'total',expression}])).columns[0].name,'total');
});
