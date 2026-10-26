import assert from 'node:assert/strict';
import test from 'node:test';
import {evaluationPreparation,messageSequence} from '../src/evaluationExecution.ts';

const suggestion={id:'case:recheck',title:'다시 비교',case_ref:{id:'case',version:2}};
const value={status:'ready',cases:[{id:'case',version:2}],suggestions:[suggestion],search:{query:'P1 관련 사례',dataset_id:'historical'}};

test('prepare preserves exact selected dataset version, card filters and period with case provenance',()=>{
 const context={project_id:7,dataset_id:'current',period_start:{year:2026,week:42},card:{dataset_id:'current',dataset_version:3,title:'P1',period:{mode:'off'},filters:[{column:'equipment',op:'eq',value:'EQ2'}]}};
 const body=evaluationPreparation(value,suggestion,context,'chat',4);
 assert.deepEqual(body,{case_ref:{id:'case',version:2},suggestion_id:'case:recheck',search:{query:'P1 관련 사례',...context},conversation_id:'chat',turn_sequence:4});
 assert.equal(body.search.dataset_id,'current');
 assert.equal(body.search.card.dataset_version,3);
 assert.deepEqual(body.search.card.period,{mode:'off'});
 assert.ok(!('message' in body),'free-form text cannot authorize analysis execution');
});

test('withdrawn or changed case and absent current data cannot prepare a run',()=>{
 const context={project_id:null,dataset_id:'current'};
 assert.throws(()=>evaluationPreparation({...value,status:'unavailable'},suggestion,context));
 assert.throws(()=>evaluationPreparation(value,{...suggestion,case_ref:{id:'case',version:3}},context));
 assert.throws(()=>evaluationPreparation(value,suggestion,{project_id:null}));
});

test('card presentation and executable scripts never enter the strict analysis scope',()=>{
 const body=evaluationPreparation(value,suggestion,{project_id:null,dataset_id:'current',card:{dataset_id:'current',dataset_version:2,title:'A',kind:'plotly',python_script:'untrusted',filters:[],period:{mode:'off'}}});
 assert.equal(body.search.card.python_script,undefined);
 assert.equal(body.search.card.kind,undefined);
 assert.deepEqual(body.search.card.filters,[]);
 assert.equal(body.search.card.dataset_version,2);
});

test('restored assistant message IDs preserve the original turn instead of newest revision',()=>{
 assert.equal(messageSequence('3-assistant'),3);
 assert.equal(messageSequence('3-user'),undefined);
 assert.equal(messageSequence(undefined),undefined);
 assert.equal(messageSequence('forged'),undefined);
});
