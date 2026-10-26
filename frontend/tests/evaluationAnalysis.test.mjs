import test from 'node:test';
import assert from 'node:assert/strict';
import {evaluationRecommendationBody} from '../src/evaluationAnalysis.ts';
import {blankEvaluationAnalysis,evaluationAnalysisBusy,evaluationAnalysisCurrent,evaluationAnalysisExecuteBody,evaluationAnalysisInput,evaluationAnalysisNumber,evaluationAnalysisReviewBody,evaluationAnalysisSettings,evaluationScopeFilters} from '../src/evaluationAnalysis.ts';

const columns=[{name:'recipe',type:'text',numeric:false},{name:'alpha',type:'real',numeric:true},{name:'epm',type:'real',numeric:true},{name:'equipment',type:'text',numeric:false}];
const input={...blankEvaluationAnalysis(),groupColumn:'recipe',metrics:['alpha','epm']};
const run={id:'run-1',workspace_token:'workspace-one',project_id:4,status:'needs_input',revision:3,columns,result:null};

test('AI setup confirmation uses the exact proposal and blocks unapproved or stale connections',()=>{
 const settings={method:'group_compare',metrics:['alpha'],group_column:'recipe',baseline:null,comparison:null,features:[],unit_columns:[],time_column:null};
 const recommended={...run,recommendation:{status:'ready',settings,questions:[{id:'question',status:'answered'}]}};
 assert.deepEqual(evaluationRecommendationBody(recommended,'workspace-one',true,60),{expected_revision:3,workspace_token:'workspace-one',settings,continuous:true,interval_minutes:60});
 assert.equal(evaluationRecommendationBody(recommended,'workspace-one',false,15).continuous,false);
 for(const patch of [{status:'completed'},{workspace_token:'other'},{recommendation:{...recommended.recommendation,status:'waiting'}},{recommendation:{...recommended.recommendation,questions:[{status:'review'}]}}])assert.throws(()=>evaluationRecommendationBody({...recommended,...patch},'workspace-one',true,60));
 assert.throws(()=>evaluationRecommendationBody(recommended,'workspace-one',true,0));
 assert.throws(()=>evaluationRecommendationBody({...recommended,recommendation:{...recommended.recommendation,settings:{...settings,metrics:['missing']}}},'workspace-one',true,60));
});

test('group comparison executes only the reviewed current columns and preserves exact group values',()=>{
 assert.deepEqual(evaluationAnalysisExecuteBody(run,input,'workspace-one'),{expected_revision:3,workspace_token:'workspace-one',settings:{method:'group_compare',group_column:'recipe',metrics:['alpha','epm']}});
 assert.deepEqual(evaluationAnalysisSettings({...input,baseline:' recipe 1 ',comparison:'recipe 2'},columns),{method:'group_compare',group_column:'recipe',metrics:['alpha','epm'],baseline:' recipe 1 ',comparison:'recipe 2'});
 for(const patch of [{metrics:[]},{metrics:['alpha','alpha']},{metrics:['alpha','epm','recipe']},{metrics:['recipe']},{metrics:['removed']},{groupColumn:'deleted'},{groupColumn:'alpha'},{baseline:'recipe 1'},{comparison:'recipe 2'},{baseline:'one',comparison:'one'}])assert.throws(()=>evaluationAnalysisSettings({...input,...patch},columns));
});

test('correlation requires two current numeric columns and drops leftover group settings',()=>{
 const value={...input,method:'correlation',baseline:'first',comparison:'last'};
 assert.deepEqual(evaluationAnalysisSettings(value,columns),{method:'correlation',metrics:['alpha','epm']});
 assert.throws(()=>evaluationAnalysisSettings({...value,metrics:['alpha']},columns));
 assert.throws(()=>evaluationAnalysisSettings({...value,metrics:['alpha','recipe']},columns));
});

test('view and execute guards reject other workspaces, projects, runs and completed requests',()=>{
 assert.equal(evaluationAnalysisCurrent(run,'run-1','workspace-one',4),true);
 for(const args of [['run-2','workspace-one',4],['run-1','workspace-two',4],['run-1','workspace-one',null]])assert.equal(evaluationAnalysisCurrent(run,...args),false);
 assert.equal(evaluationAnalysisCurrent(undefined,'run-1','workspace-one',4),false);
 assert.throws(()=>evaluationAnalysisExecuteBody(run,input,'workspace-two'));
 for(const status of ['queued','running','completed','failed','cancelled'])assert.throws(()=>evaluationAnalysisExecuteBody({...run,status},input,'workspace-one'));
});

test('knowledge review uses the completed server result and revision, never caller-supplied numbers',()=>{
 const completed={...run,status:'completed',result:{summary:'server calculation',row_count:50}};
 assert.deepEqual(evaluationAnalysisReviewBody(completed,'workspace-one','  조건 확인 요청  '),{expected_revision:3,workspace_token:'workspace-one',note:'조건 확인 요청'});
 assert.deepEqual(evaluationAnalysisReviewBody(completed,'workspace-one',' '),{expected_revision:3,workspace_token:'workspace-one'});
 assert.throws(()=>evaluationAnalysisReviewBody(run,'workspace-one',''));
 assert.throws(()=>evaluationAnalysisReviewBody({...completed,result:null},'workspace-one',''));
 assert.throws(()=>evaluationAnalysisReviewBody({...completed,result:{...completed.result,knowledge_eligible:false}},'workspace-one',''));
 assert.throws(()=>evaluationAnalysisReviewBody(completed,'workspace-two',''));
});

test('restoring settings copies metrics and preserves calculation method without starting execution',()=>{
 const settings={method:'group_compare',metrics:['alpha'],group_column:'recipe',baseline:'A',comparison:'B'};
 const restored=evaluationAnalysisInput(settings);restored.metrics.push('epm');
 assert.deepEqual(settings.metrics,['alpha']);assert.deepEqual(evaluationAnalysisInput(null),blankEvaluationAnalysis());
 for(const status of ['queued','running','cancelling'])assert.equal(evaluationAnalysisBusy({status}),true);
 for(const status of ['needs_input','completed','failed'])assert.equal(evaluationAnalysisBusy({status}),false);
 assert.equal(evaluationAnalysisBusy(undefined),false);
});

test('scope and missing results are shown without estimating or replacing server numbers',()=>{
 assert.deepEqual(evaluationScopeFilters([{column:'equipment',op:'in',value:['E1','E2']},{column:'alpha',op:'gte',value:0}]),['equipment 포함 E1, E2','alpha ≥ 0']);
 assert.equal(evaluationAnalysisNumber(0),'0');
 for(const value of [null,undefined,NaN,Infinity])assert.equal(evaluationAnalysisNumber(value),'—');
});

test('ML requires a separate target, features, sample identity and real measurement time',()=>{
 const all=[...columns,{name:'lot',type:'text',numeric:false},{name:'measured',type:'timestamp with time zone',numeric:false}];
 const value={...blankEvaluationAnalysis(),method:'ml_regression',metrics:['epm'],features:['alpha','recipe'],unitColumns:['lot'],timeColumn:'measured'};
 assert.deepEqual(evaluationAnalysisSettings(value,all),{method:'ml_regression',metrics:['epm'],features:['alpha','recipe'],unit_columns:['lot'],time_column:'measured'});
 for(const patch of [{features:[]},{features:['epm']},{unitColumns:[]},{timeColumn:'recipe'},{unitColumns:['alpha']},{features:['deleted']},{metrics:['epm','alpha']}])assert.throws(()=>evaluationAnalysisSettings({...value,...patch},all));
 assert.deepEqual(evaluationAnalysisInput(evaluationAnalysisSettings(value,all)),value);
});
