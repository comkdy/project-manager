import test from 'node:test';
import assert from 'node:assert/strict';
import {blankEvaluationInput,evaluationDatasetNotice,evaluationRegistrationBody,evaluationScopeLabel,validEvaluationInput} from '../src/evaluationKnowledge.ts';
import {definitionInput,validDefinitionInput,validReviewInput} from '../src/semanticDrafts.ts';
import {draftReviewInput,initialDraftReview} from '../src/draftReviewState.ts';
import {draftKey,readLocalDraft} from '../src/localDraftStorage.ts';
import {confirmationInProject,confirmationStep} from '../src/confirmationRequests.ts';

const datasets=[{id:'project-epm',name:'EPM',version:2,project_id:5},{id:'shared-inline',name:'Inline',version:1,project_id:null}];
const content={name:'Recipe 평가',description:'Recipe 변경 시 alpha 증가, parameter1 감소를 관측',domain:'공정',aliases:[],unresolved:[],references:[],change_note:'',details:{operation:'A',recipe_changes:'Recipe 1, 2, 3',inline_observations:'alpha 증가',epm_observations:'parameter1 감소',conditions:'제품 P, 장비 E',evidence:'평가 보고서',limitations:'현재 데이터에서 재확인 필요'}};

test('scope and natural language register without any dataset and validate project identity',()=>{
 assert.deepEqual(evaluationRegistrationBody({projectId:null,text:' 공통 평가 '},[]),{project_id:null,text:'공통 평가'});
 assert.deepEqual(evaluationRegistrationBody({projectId:17,text:'프로젝트 평가'},[{id:17,name:'XXXA'}]),{project_id:17,text:'프로젝트 평가'});
 assert.throws(()=>evaluationRegistrationBody({projectId:18,text:'평가'},[{id:17,name:'XXXA'}]));
 assert.throws(()=>evaluationRegistrationBody({projectId:null,text:'평가',approved:true},[]));
 assert.deepEqual(blankEvaluationInput(),{projectId:null,text:''});
});

test('dataset-free confirmation keeps scope, question, approval and completion states',()=>{
 const q={dataset:null,case_context:{project_id:17},status:'review',dataset_changed:false,followups:['alpha의 의미는?']};
 assert.equal(confirmationInProject(q,'17'),true);assert.equal(confirmationInProject(q,'shared'),false);
 assert.equal(confirmationStep(q),'answer');
 assert.equal(confirmationStep({...q,followups:[]}),'approval');
 assert.equal(confirmationStep({...q,status:'answered'}),'completed');
 assert.equal(confirmationInProject({...q,case_context:{project_id:null}},'shared'),true);
});

test('reviewed term provenance survives definition recovery without arbitrary metadata',()=>{
 const term={term:'alpha',status:'linked',explanation:'A 공정 선폭',answer_id:'answer',quote:'alpha는 A 공정 선폭',references:[{item_id:'width',version:2,name:'선폭',binding_version:3,binding_fingerprint:'approved-binding'}]};
 const definition=definitionInput('evaluation','database',{...content,details:{...content.details,terms:[term]}});
 assert.equal(validDefinitionInput(definition),true);
 const invalid=structuredClone(definition);invalid.content.details.terms[0].raw_rows=[{secret:1}];
 assert.equal(validDefinitionInput(invalid),false);
});

test('evaluation contribution keeps only user text and selected visible dataset',()=>{
 const value={datasetId:'project-epm',text:'  Recipe 1 → 2 평가\nalpha 증가, parameter1 감소  '};
 assert.deepEqual(evaluationRegistrationBody(value,datasets),{dataset_id:'project-epm',text:'Recipe 1 → 2 평가\nalpha 증가, parameter1 감소'});
 for(const rejected of [{...value,datasetId:'deleted'}, {...value,text:' \n '}, {...value,text:'x'.repeat(6001)}, {...value,approved:true}]){
  assert.throws(()=>evaluationRegistrationBody(rejected,datasets));
 }
 assert.throws(()=>evaluationRegistrationBody(value,[]),'a cached selection cannot authorize a hidden or removed dataset');
});

test('evaluation drafts preserve incomplete input but cannot restore arbitrary metadata',()=>{
 assert.equal(validEvaluationInput(blankEvaluationInput()),true);
 assert.equal(validEvaluationInput({datasetId:'project-epm',text:'작성 중\n'}),true);
 assert.equal(validEvaluationInput({datasetId:'project-epm'}),false);
 assert.equal(validEvaluationInput({datasetId:4,text:'내용'}),false);
 assert.equal(validEvaluationInput({datasetId:'project-epm',text:'내용',rows:[{secret:1}]}),false);
 assert.equal(validEvaluationInput({datasetId:'project-epm',text:'x'.repeat(6001)}),false);
});

test('evaluation drafts are isolated by existing user and workspace draft identity',()=>{
 const identity={userId:'engineer',role:'viewer',workspace:'db-one',projectId:null,kind:'knowledge-evaluation',targetId:'new'};
 const value={datasetId:'project-epm',text:'아직 작성 중인 평가 내용'};
 const raw=JSON.stringify({schema:1,identity,baseVersion:1,value,savedAt:123,writer:'tab-a'});
 assert.deepEqual(readLocalDraft(raw,identity,validEvaluationInput).value,value);
 for(const patch of [{userId:'another'},{workspace:'db-two'},{role:'admin'},{kind:'knowledge-answer'}]){
  const other={...identity,...patch};
  assert.notEqual(draftKey(identity),draftKey(other));
  assert.throws(()=>readLocalDraft(raw,other,validEvaluationInput));
 }
});

test('evaluation scope labels distinguish shared datasets and project datasets',()=>{
 assert.equal(evaluationScopeLabel(datasets[0],[{id:5,name:'제품 P'}]),'제품 P');
 assert.equal(evaluationScopeLabel(datasets[0],[]),'프로젝트 #5');
 assert.equal(evaluationScopeLabel(datasets[1],[]),'공통 데이터셋');
});

test('changed evaluation datasets explain search exclusion while preserving the reported origin',()=>{
 const context={dataset:datasets[0],question_id:'original-question',dataset_changed:true};
 const original=JSON.stringify(context);
 assert.match(evaluationDatasetNotice(context),/관련 사례 검색에서 제외/);
 assert.match(evaluationDatasetNotice(context),/현재 데이터셋으로 사례를 다시 등록하고 검토/);
 assert.equal(JSON.stringify(context),original,'the original dataset version and question remain available');
 for(const value of [undefined,null,{}, {dataset_changed:false}])assert.equal(evaluationDatasetNotice(value),null);
});

test('evaluation definitions and approval drafts retain observation boundaries through existing edit recovery',()=>{
 assert.equal(validDefinitionInput(definitionInput('evaluation','database',content)),true);
 const state=initialDraftReview({id:'case-draft',question_id:'case-question',answer_id:'case-answer',status:'pending',revision:3,proposal:{kind:'evaluation',scope:'database',content},question:{dataset:{project_id:5},dataset_changed:false,status:'review'}});
 const value=draftReviewInput(state);
 assert.equal(validReviewInput(value),true);
 assert.equal(value.proposal.content.details.limitations,'현재 데이터에서 재확인 필요');
 assert.equal(value.proposal.scope,'database');
 assert.equal('question' in value,false,'server origin and original answers are not copied into local draft content');
});
