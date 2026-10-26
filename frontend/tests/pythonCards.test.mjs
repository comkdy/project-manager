import test from 'node:test';
import assert from 'node:assert/strict';
import {applyPythonConversion,blankPythonCard,canUseSimpleEditor,needsScriptExpansion} from '../src/pythonCards.ts';

test('new Python draft keeps dataset and filters without changing the original card',()=>{
 const original={id:'c',dataset_id:'d',dataset_version:2,kind:'bar',aggregate:'avg',x:'lot',y:'value',filters:[{column:'id',op:'eq',value:3}],period:{use_dashboard:false},options:{palette:'warm'}};
 const converted=blankPythonCard(original);
 assert.match(converted.python_script,/fig = go.Figure\(\)/);
 assert.deepEqual(converted.filters,original.filters);assert.deepEqual(converted.options,original.options);
 assert.equal(converted.script_input,'dataset');assert.equal(original.editor_mode,undefined);
 assert.equal(canUseSimpleEditor(converted),false);
});

test('only the unmodified full generated source can return to the simple editor',()=>{
 const baseline='fig = go.Figure()\nfig.add_trace(go.Bar(x=df["lot"], y=df["value"]))';
 const converted={dataset_id:'d',dataset_version:2,kind:'bar',renderer:'plotly',x:'lot',y:'value',aggregate:'avg',options:{palette:'teal',sort:'asc'},script_input:'dataset',python_script:baseline};
 assert.equal(canUseSimpleEditor(converted,baseline,converted),true);
 assert.equal(canUseSimpleEditor(converted),false);
 assert.equal(canUseSimpleEditor(converted,baseline),false);
 assert.equal(canUseSimpleEditor({...converted,python_script:baseline+'\nfig.update_layout(showlegend=False)'},baseline,converted),false);
 for(const change of [{dataset_id:'other'},{dataset_version:3},{kind:'line'},{y:'other_value'},{aggregate:'sum'},{options:{palette:'warm',sort:'asc'}}]){
  assert.equal(canUseSimpleEditor({...converted,...change},baseline,converted),false);
 }
 assert.equal(canUseSimpleEditor({...converted,title:'New title',filters:[{column:'lot',op:'eq',value:'A'}],period:{use_dashboard:false},options:{sort:'asc',palette:'teal'}},baseline,converted),true);
});

test('every legacy chart-input script is migrated, while raw dataset scripts are preserved',()=>{
 assert.equal(needsScriptExpansion({script_input:'chart',python_script:'fig.update_layout(hovermode="closest")'}),true);
 assert.equal(needsScriptExpansion({script_input:'chart',python_script:'# heading\nfig = go.Figure()'}),true);
 assert.equal(needsScriptExpansion({script_input:'dataset',python_script:'fig.update_layout(showlegend=False)'}),false);
});

test('conversion changes the legacy input and source together without replacing edits or query scope',()=>{
 const legacy={dataset_id:'d',dataset_version:1,title:'Edited title',script_input:'chart',python_script:'fig = go.Figure()',filters:[{column:'lot',op:'eq',value:'A'}],period:{use_dashboard:false}};
 const conversion={card:{...legacy,title:'Old title',dataset_version:2,script_input:'dataset',python_script:'fig = px.scatter(df, x="lot", y="value")'},simple_script:'baseline',input_columns:[{name:'lot',description:'lot'}]};
 const migrated=applyPythonConversion(legacy,conversion);
 assert.equal(migrated.script_input,'dataset');assert.equal(migrated.python_script,conversion.card.python_script);assert.equal(migrated.dataset_version,2);
 assert.equal(migrated.title,'Edited title');assert.deepEqual(migrated.filters,legacy.filters);assert.deepEqual(migrated.period,legacy.period);
 assert.equal(legacy.script_input,'chart');assert.equal(legacy.python_script,'fig = go.Figure()');
 const custom={...legacy,script_input:'dataset',python_script:'fig = px.scatter(df, x="my_custom_x", y="my_custom_y")'};
 assert.equal(applyPythonConversion(custom,conversion),custom);
});
