import test from 'node:test';
import assert from 'node:assert/strict';
import {dataAnalysisPending,dataAnalysisReviewable,dataAnalysisPath,chatSelectedDataset} from '../src/chatDataAnalysis.ts';

test('worker messages keep refreshing until calculation or questions resolve',()=>{
 for(const status of ['queued','running','needs_input','cancelling'])assert.equal(dataAnalysisPending({status}),true);
 for(const status of ['completed','failed','cancelled','unavailable'])assert.equal(dataAnalysisPending({status}),false);
});
test('dashboard analysis does not inherit an unrelated dataset selected on another screen',()=>{
 assert.equal(chatSelectedDataset('dashboard',null,'current_status'),null);
 assert.equal(chatSelectedDataset('dashboard',{dataset_id:'epm-card'},'current_status'),'epm-card');
 assert.equal(chatSelectedDataset('data',{dataset_id:'epm-card'},'joined-data'),'joined-data');
 assert.equal(chatSelectedDataset('knowledge',{dataset_id:'epm-card'},'current_status'),null);
});
test('only valid completed computations offer knowledge review',()=>{
 const value={status:'completed',result:{row_count:20,knowledge_eligible:true}};
 assert.equal(dataAnalysisReviewable(value),true);
 assert.equal(dataAnalysisReviewable({...value,status:'running'}),false);
 assert.equal(dataAnalysisReviewable({...value,result:{row_count:0}}),false);
 assert.equal(dataAnalysisReviewable({...value,result:{row_count:20,knowledge_eligible:false}}),false);
 assert.equal(dataAnalysisPath('a/b',null),'/chat/data-analysis/a%2Fb');
 assert.equal(dataAnalysisPath('run',4),'/chat/data-analysis/run?project_id=4');
});
