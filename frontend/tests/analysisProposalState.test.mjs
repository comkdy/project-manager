import assert from 'node:assert/strict';
import test from 'node:test';
import {proposalConfirmBody,proposalEditorRequest,proposalFixedTargetLabel,proposalReadyToConfirm,proposalRemainingBlockers} from '../src/analysisProposalState.ts';

const source={proposal_id:'plan-1',revision:1,status:'pending',workspace_token:'db-A',project_id:7,project_scope:{id:7,name:'프로젝트 A',lot_cds:['L7']},request:{kind:'trend',purpose:'analysis',project_id:7,family:'epm',parameter:'P1',operation:null,baseline_start:'2026-10-01T00:00:03.123456+00:00',baseline_end:null,retest_policy:null,min_samples:null,absolute_delta:null,knowledge_refs:[{item_id:'approved',version:3}]},explanation:'기간 확인',missing:['period'],blockers:['analysis_method']};
const ready={isSuccess:true,isFetching:false,isError:false};

test('proposal form restores only supplied conditions and keeps exact date and knowledge versions',()=>{
 const before=structuredClone(source),request=proposalEditorRequest(source);
 assert.equal(request.baseline_start,source.request.baseline_start);
 assert.equal(request.baseline_end,undefined);
 assert.equal(request.retest_policy,undefined);
 assert.equal(request.min_samples,undefined);
 assert.deepEqual(request.knowledge_refs,[{item_id:'approved',version:3}]);
 assert.deepEqual(request._project_scope,source.project_scope);
 assert.deepEqual(source,before,'opening the form never changes the server snapshot');
 assert.equal(proposalEditorRequest({...source,status:'unavailable',request:null}),null);
});

test('confirm fixes kind, purpose and project to the reviewed proposal and removes display-only scope',()=>{
 const edited={kind:'statistics',purpose:'validation',project_id:99,_project_scope:{id:99},current_start:'2026-10-20T00:00:00Z',retest_policy:'all_events',min_samples:5,absolute_delta:0,knowledge_refs:[{item_id:'approved',version:3}]};
 const body=proposalConfirmBody(source,edited);
 assert.equal(body.expected_revision,1);assert.equal(body.workspace_token,'db-A');assert.equal(body.project_id,7);
 assert.equal(body.request.kind,'trend');assert.equal(body.request.purpose,'analysis');assert.equal(body.request.project_id,7);
 assert.equal(body.request._project_scope,undefined);
 assert.equal(body.request.current_start,edited.current_start);
 assert.equal(body.request.absolute_delta,0);
 assert.deepEqual(body.request.knowledge_refs,edited.knowledge_refs);
 assert.equal(source.blockers[0],'analysis_method','the UI cannot clear unsupported-method restrictions');
});

test('statistics keeps server target fields which are absent from the existing statistics form',()=>{
 const proposal={...source,request:{kind:'statistics',family:'inline',operation:'oper 1',parameter:'P9'}};
 const body=proposalConfirmBody(proposal,{kind:'statistics',start:'2026-10-01T00:00:00Z'});
 assert.equal(body.request.family,'inline');assert.equal(body.request.operation,'oper 1');assert.equal(body.request.parameter,'P9');
 assert.equal(proposalFixedTargetLabel(proposal),'Inline · 공정 oper 1 · P9');
});

test('multi-PARA changes preserve and display the fixed source and operation instead of broadening to every source',()=>{
 const proposal={...source,request:{kind:'trend',family:'epm',parameter:null,operation:'oper 2',bucket:'day'}};
 const body=proposalConfirmBody(proposal,{kind:'trend',baseline_start:'2026-10-01T00:00:00Z'});
 assert.equal(body.request.family,'epm');assert.equal(body.request.operation,'oper 2');assert.equal(body.request.parameter,null);assert.equal(body.request.bucket,'day');
 assert.equal(proposalFixedTargetLabel(proposal),'EPM · 공정 oper 2 · 전체 PARA');
 const single=proposalConfirmBody(source,{kind:'trend',family:'inline',operation:'other',parameter:'P2'});
 assert.equal(single.request.family,'inline');assert.equal(single.request.operation,'other');assert.equal(single.request.parameter,'P2');
 assert.equal(proposalFixedTargetLabel(source),null);
});

test('filling missing periods clears only the period reminder; catalog and unsupported conditions still need server validation',()=>{
 const proposal={...source,blockers:['period','analysis_target','analysis_scope','analysis_method']};
 assert.deepEqual(proposalRemainingBlockers(proposal,false),proposal.blockers);
 assert.deepEqual(proposalRemainingBlockers(proposal,true),['analysis_target','analysis_scope','analysis_method']);
});

test('clearing an editable condition does not silently merge its original value back in',()=>{
 const proposal={...source,request:{...source.request,min_samples:50,absolute_delta:4,retest_policy:'all_events'}};
 const body=proposalConfirmBody(proposal,{kind:'trend'});
 assert.equal(body.request.min_samples,undefined);assert.equal(body.request.absolute_delta,undefined);assert.equal(body.request.retest_policy,undefined);
});

test('failed, still loading, changed, unavailable, or confirmed proposals never enable confirmation',()=>{
 assert.equal(proposalReadyToConfirm(source,source,'db-A',ready),true);
 for(const query of [{...ready,isFetching:true},{...ready,isError:true},{...ready,isSuccess:false}])assert.equal(proposalReadyToConfirm(source,source,'db-A',query),false);
 for(const latest of [undefined,{...source,revision:2},{...source,proposal_id:'plan-2'},{...source,project_id:8},{...source,workspace_token:'db-B'},{...source,status:'confirmed'},{...source,status:'unavailable'}])assert.equal(proposalReadyToConfirm(source,latest,'db-A',ready),false);
 assert.equal(proposalReadyToConfirm(source,source,'db-B',ready),false);
 for(const proposal of [{...source,status:'confirmed'},{...source,request:null},{...source,revision:null},{...source,workspace_token:null}])assert.throws(()=>proposalConfirmBody(proposal,{kind:'trend'}),/다시 확인/);
});
