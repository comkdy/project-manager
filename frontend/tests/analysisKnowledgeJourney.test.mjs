import assert from 'node:assert/strict';
import test from 'node:test';
import {analysisJourneyCurrent,analysisReturnValid,approvedAnalysisDefinitions,createAnalysisRestoreGuard,exactQuestionDraft,questionDraftTarget} from '../src/analysisSelection.ts';
import {analysisDateInput,analysisRequestDate} from '../src/analysisDates.ts';

const journey={workspace:'db-A',userId:'user-1',role:'admin',runId:'run-1',projectId:3,questionId:'question-1',questionTitle:'기준',definition:null};
const definition={item_id:'criterion-1',version:2,content:{name:'기준',description:'최소 표본 5, 평균 변화 2',details:{}}};
const original=()=>({id:'run-1',request:{kind:'trend',project_id:3,min_samples:null,absolute_delta:null,knowledge_refs:[{item_id:'independent-criterion',version:1}]},questions:[{id:'unrelated-question'},{id:'question-1',approved_definition:definition}]});

test('a question journey and pending resume belong to the exact account, role and workspace',()=>{
 assert.equal(analysisJourneyCurrent(journey,'db-A','user-1','admin'),true);
 for(const args of [['db-B','user-1','admin'],['db-A','user-2','admin'],['db-A','user-1','viewer'],['db-A',undefined,undefined]])assert.equal(analysisJourneyCurrent(journey,...args),false);
 assert.equal(analysisJourneyCurrent(null,'db-A','user-1','admin'),false);
 const resume={workspace:'db-A',userId:'user-1',role:'admin',run:original(),token:1};
 assert.equal(analysisJourneyCurrent(resume,'db-B','user-1','admin'),false,'old resume data must not mount in the next DB');
});

test('return resolves only the original analysis and question in its project scope',()=>{
 const run=original();
 assert.equal(analysisReturnValid(journey,run),true);
 assert.equal(analysisReturnValid(journey,{...run,id:'run-2'}),false);
 assert.equal(analysisReturnValid(journey,{...run,questions:[{id:'unrelated-question'}]}),false);
 assert.equal(analysisReturnValid(journey,{...run,request:{...run.request,project_id:4}}),false);
 assert.equal(analysisReturnValid(journey,{...run,request:{kind:'trend',_project_scope:{id:3}}}),true);
});

test('an observed approval cannot be reused after revocation or a newer approval',()=>{
 const pinned={...journey,definition:{item_id:definition.item_id,version:definition.version}},run=original();
 assert.equal(analysisReturnValid(pinned,run),true);
 for(const next of [undefined,{...definition,version:3},{...definition,item_id:'different-criterion'}]){
  assert.equal(analysisReturnValid(pinned,{...run,questions:[{id:journey.questionId,approved_definition:next}]}),false);
 }
});

test('approved descriptions remain evidence and never populate missing conditions or alter existing references',()=>{
 const run=original(),before=structuredClone(run);
 assert.deepEqual(approvedAnalysisDefinitions(run),[definition]);
 assert.equal(analysisReturnValid(journey,run),true);
 assert.deepEqual(run,before);
 assert.equal(run.request.min_samples,null);assert.equal(run.request.absolute_delta,null);
 assert.deepEqual(run.request.knowledge_refs,[{item_id:'independent-criterion',version:1}]);
 assert.deepEqual(approvedAnalysisDefinitions({...run,questions:[]}),[]);
});

test('exact ready answer and pending draft are required for admin review navigation',()=>{
 const q={id:'question-1',status:'review',dataset_changed:false,answers:[{id:'answer-1',status:'ready'}],draft:{id:'draft-1',question_id:'question-1',answer_id:'answer-1',status:'pending'}};
 assert.equal(questionDraftTarget(q,true),q.draft);
 assert.equal(questionDraftTarget(q,false),null);
 for(const patch of [{status:'structuring'},{dataset_changed:true},{answers:[{id:'answer-other',status:'ready'}]},{answers:[{id:'answer-1',status:'error'}]},{draft:{...q.draft,question_id:'question-other'}},{draft:{...q.draft,status:'superseded'}},{draft:null}])assert.equal(questionDraftTarget({...q,...patch},true),null);
});

test('direct draft detail must match both the question and the answer, without a list fallback',()=>{
 const draft={question_id:'question-1',question:{id:'question-1'},answer_id:'answer-1'};
 assert.equal(exactQuestionDraft('question-1','answer-1',draft),true);
 assert.equal(exactQuestionDraft('question-2','answer-1',draft),false);
 assert.equal(exactQuestionDraft('question-1','answer-2',draft),false);
 assert.equal(exactQuestionDraft('question-1','answer-1',{...draft,question:{id:'question-2'}}),false);
});

test('a late restore cannot replace another selection or a newer restore',()=>{
 const guard=createAnalysisRestoreGuard();
 const earlier=guard.start('run-A'),later=guard.start('run-B');
 assert.equal(guard.accepts(earlier,'run-B',true),false);
 assert.equal(guard.accepts(earlier,'run-A',true),false,'a newer request wins even after revisiting A');
 assert.equal(guard.accepts(later,'run-B',true),true);
 assert.equal(guard.accepts(later,'run-B',false),false,'hidden workspaces cannot restore inputs');
});

test('editing the form or leaving and returning invalidates an in-flight restore',()=>{
 const guard=createAnalysisRestoreGuard(),pending=guard.start('run-A');
 guard.invalidate();
 assert.equal(guard.accepts(pending,'run-A',true),false);
 const next=guard.start('run-A');
 assert.equal(guard.accepts(next,'run-A',true),true);
});

test('restoring an unchanged timestamp preserves seconds, fractions and original timezone precision',()=>{
 for(const original of ['2026-09-21T10:30:37.500000+00:00','2026-09-21T19:30:37.123456+09:00','2026-09-21T00:59:16Z']){
  const input=analysisDateInput(original);
  assert.match(input,/T\d{2}:\d{2}:\d{2}\.\d{3}$/);
  assert.equal(new Date(input).getTime(),new Date(original).getTime());
  assert.equal(analysisRequestDate(input,original),original);
 }
});

test('manually changed dates use the entered time while empty dates stay unfilled',()=>{
 const original='2026-09-21T10:30:37.123456+00:00';
 const changed='2026-09-22T10:30:38.125';
 assert.equal(analysisRequestDate(changed,original),new Date(changed).toISOString());
 assert.equal(analysisDateInput(undefined),'');
});
