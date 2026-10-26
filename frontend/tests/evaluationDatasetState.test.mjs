import test from 'node:test';
import assert from 'node:assert/strict';
import {preferredSetupEntry,recommendedDatasetPrepare} from '../src/evaluationDatasetState.ts';

test('returning from a newer dataset question cannot reopen an older analysis setup',()=>{
 const run={id:'old-analysis',project_id:3,entry:10},dataset={id:'new-dataset',entry:20};
 assert.equal(preferredSetupEntry(run,dataset).kind,'dataset');
 assert.equal(preferredSetupEntry({...run,entry:30},dataset).kind,'analysis');
 assert.equal(preferredSetupEntry(null,dataset).value.id,dataset.id);
 assert.equal(preferredSetupEntry(run,null).value.id,run.id);
 assert.equal(preferredSetupEntry(null,null),null);
});
import {proposalDefinition} from '../src/chatProposal.ts';

test('dataset confirmation preserves scope and refuses unfinished or changed connections',()=>{
 const value={id:'recommendation',workspace_token:'workspace',status:'ready',dataset_id:'comparison',project_id:3,case_ref:{id:'case',version:2},case_name:'Recipe 평가',questions:[{status:'answered'}]};
 assert.deepEqual(recommendedDatasetPrepare(value,'workspace'),{explicit_case:true,dataset_recommendation_id:'recommendation',case_ref:value.case_ref,suggestion_id:'case:recheck',search:{query:value.case_name,dataset_id:'comparison',project_id:3}});
 for(const patch of [{status:'waiting'},{dataset_id:null},{workspace_token:'previous'},{questions:[{status:'review'}]}])assert.throws(()=>recommendedDatasetPrepare({...value,...patch},'workspace'));
 assert.equal(recommendedDatasetPrepare({...value,status:'applied'},'workspace').search.dataset_id,'comparison');
});

test('existing dataset review keeps recommendation provenance and excludes preview rows',()=>{
 const value={kind:'dataset',explanation:'새 비교 데이터',evaluation_dataset_recommendation_id:'recommendation',dataset:{name:'comparison',spec:{source_id:'source',raw_sql:'SELECT id FROM measurements'}},query_result:{rows:[{raw:'data'}]}};
 const request=proposalDefinition(value);
 assert.equal(request.evaluation_dataset_recommendation_id,'recommendation');
 assert.equal('query_result' in request,false);
 assert.equal(proposalDefinition({...value,dataset:{...value.dataset,name:'edited'}}).evaluation_dataset_recommendation_id,'recommendation');
});
