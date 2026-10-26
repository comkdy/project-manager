import test from 'node:test';
import assert from 'node:assert/strict';
import {jobActionBody,jobActionCurrent,jobActionExplanation} from '../src/jobActions.ts';
import {activitySummary,activityStatusNames} from '../src/workspaceActivity.ts';
import {proposalReadyToConfirm} from '../src/analysisProposalState.ts';

const target={kind:'analysis_run',id:'run1',project_id:7},controls={revision:4,can_cancel:true};
test('confirmation captures exact workspace, project and observed revision',()=>{
 const body=jobActionBody(target,controls,'cancel','db:2');
 assert.deepEqual(body,{target,action:'cancel',expected_revision:4,workspace_token:'db:2'});
 assert.notEqual(body.target,target);
 assert.equal(jobActionCurrent(body,target,controls,'db:2'),true);
 for(const changed of [{...controls,revision:5},{...controls,can_cancel:false},undefined])assert.equal(jobActionCurrent(body,target,changed,'db:2'),false);
 assert.equal(jobActionCurrent(body,target,controls,'db:3'),false);
 for(const changed of [{...target,id:'run2'},{...target,project_id:8},{...target,kind:'analysis_proposal'}])assert.equal(jobActionCurrent(body,changed,controls,'db:2'),false);
});
test('retry requires server capability even if cancellation is permitted',()=>{
 const body=jobActionBody(target,controls,'retry','db:2');
 assert.equal(jobActionCurrent(body,target,controls,'db:2'),false);
 assert.equal(jobActionCurrent(body,target,{...controls,can_retry:true},'db:2'),true);
});
test('cancelling is active; cancelled is not reported completed or failed',()=>{
 assert.deepEqual(activitySummary([{status:'cancelling'},{status:'cancelled'}]),{active:1,attention:0,completed:0});
 assert.equal(activityStatusNames.cancelling,'취소 요청됨');assert.equal(activityStatusNames.cancelled,'취소 완료');
});
test('action explanations distinguish a new source read, proposal withdrawal and cooperative stop',()=>{
 assert.match(jobActionExplanation(target,'retry'),/원천 데이터는 다시 읽/);
 assert.match(jobActionExplanation(target,'cancel'),/기존 질문·답변·승인은 유지/);
 assert.match(jobActionExplanation({kind:'analysis_proposal'},'cancel'),/새 분석이나 질문은 만들지/);
 assert.match(jobActionExplanation({kind:'knowledge_collection'},'retry'),/중복 생성하지/);
});
test('a cancelled proposal can never be confirmed from an old pending editor',()=>{
 const original={status:'pending',proposal_id:'proposal',revision:1,workspace_token:'db:2'};
 const current={...original,status:'cancelled',revision:2};
 assert.equal(proposalReadyToConfirm(original,current,'db:2',{isSuccess:true,isFetching:false,isError:false}),false);
});
