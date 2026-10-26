import assert from 'node:assert/strict';
import test from 'node:test';
import {blankQuestionInput,definitionInput,definitionVersion,mappingCanRebase,mappingVersion,questionCanAnswer,validAnswer,validDefinitionInput,validKnowledgeContent,validMappingInput,validNewQuestionInput,validReviewInput} from '../src/semanticDrafts.ts';
import {draftReviewChanged,draftReviewInput,draftReviewReducer,draftTargetChanged,initialDraftReview} from '../src/draftReviewState.ts';
import {draftKey,readLocalDraft,writeLocalDraft} from '../src/localDraftStorage.ts';

const content=()=>({name:'기간 기준',domain:'품질',description:'첫 줄\n둘째 줄',aliases:['동의어'],unresolved:['시간대 미정'],references:[{item_id:'approved-criterion',version:7}],details:{time_window:'명시 기간',timezone:null},change_note:'검토 중'});
const item=()=>({id:'item-1',kind:'metric',scope:'database',version:2,revision:5,status:'draft',content:content(),binding:{version:1,revision:3,status:'draft',content:{sources:[],predicates:[],expression:'',notes:''}}});
const collected=(revision=1)=>({id:'draft-1',question_id:'question-1',answer_id:'answer-1',revision,status:'pending',proposal:{kind:'metric',scope:'database',content:content()},original_proposal:{secret:'must not persist'},evidence:{source:'server answer'},question:{id:'question-1',dataset:{project_id:3},dataset_changed:false,status:'review',answers:[{text:'Another user answer'}]}});
const identity={userId:'user-1',role:'admin',workspace:'db-1:revision-4',projectId:3,kind:'knowledge-collected-draft',targetId:'draft-1'};

test('definition recovery preserves incomplete multiline input and exact approved reference versions',()=>{
 const value={...definitionInput('metric','database',content()),aliasesText:'동의어\n\n추가 중 ',unresolvedText:'시간대 미정\n'};
 assert.equal(validDefinitionInput(value),true);
 const roundTrip=JSON.parse(JSON.stringify(value));
 assert.equal(roundTrip.aliasesText,'동의어\n\n추가 중 ');
 assert.equal(roundTrip.unresolvedText,'시간대 미정\n');
 assert.deepEqual(roundTrip.content.references,[{item_id:'approved-criterion',version:7}]);
 assert.equal(roundTrip.content.details.timezone,null);
});

test('local definition and mapping validators accept edit fields only',()=>{
 const value=definitionInput('metric','database',content());
 for(const extra of [{rows:[{business:'data'}]},{api_key:'secret'},{validation:{}},{created_by:'user'}])assert.equal(validDefinitionInput({...value,...extra}),false);
 assert.equal(validKnowledgeContent({...content(),references:[{item_id:'criterion',version:0}]}),false);
 assert.equal(validKnowledgeContent({...content(),references:[{item_id:'criterion',version:7,approved:true}]}),false);
 const mapping={binding:{sources:[{source_id:'source-1',role:'left',columns:['LOT_CD']}],expression:'',notes:'작성 중\n'},predicates:[{source_id:'source-1',column:'VALUE',op:'gt',value_kind:'number',value_text:'-'}]};
 assert.equal(validMappingInput(mapping),true,'an unfinished numeric edit must be recoverable without coercion');
 assert.equal(validMappingInput({...mapping,binding:{...mapping.binding,rows:[1]}}),false);
 assert.equal(validMappingInput({...mapping,predicates:[{...mapping.predicates[0],value:20}]}),false,'only editable predicate text is stored');
});

test('definition and mapping snapshots detect version, approval and retirement changes',()=>{
 const original=item();
 for(const patch of [{version:3},{revision:6},{status:'approved'},{status:'retired'}]){
  assert.notEqual(definitionVersion({...original,...patch}),definitionVersion(original));
  assert.notEqual(mappingVersion({...original,...patch}),mappingVersion(original));
 }
 for(const patch of [{version:2},{revision:4},{status:'approved'},{status:'retired'}])assert.notEqual(mappingVersion({...original,binding:{...original.binding,...patch}}),mappingVersion(original));
 assert.equal(definitionVersion(null),'new');
});

test('collected draft persistence excludes server answers, evidence, original proposal and metadata',()=>{
 const state=initialDraftReview(collected());
 const value=draftReviewInput({...state,note:'내 검토 사유',target:'item-1',targetRevision:5});
 assert.equal(validReviewInput(value),true);
 assert.deepEqual(Object.keys(value).sort(),['dirty','note','proposal','target','targetRevision','unresolved']);
 for(const forbidden of ['question','base','latest','original_proposal','evidence','answers'])assert.equal(forbidden in value,false);
 assert.equal(validReviewInput({...value,base:state.base}),false);
});

test('restoring an old collected draft cannot silently adopt a newer approval revision',()=>{
 const old=initialDraftReview(collected(1)),value={...draftReviewInput(old),note:'기존 검토 사유',target:'item-1',targetRevision:5};
 const current=initialDraftReview(collected(3));
 const restored=draftReviewReducer(current,{type:'restore',value,revision:1});
 assert.equal(restored.editRevision,1);
 assert.equal(restored.latest.revision,3);
 assert.equal(draftReviewChanged(restored),true);
 assert.equal(restored.note,value.note);
 assert.deepEqual(restored.proposal.content.references,content().references);
});

test('restored changed proposal requires a save even if its local dirty flag was false',()=>{
 const state=initialDraftReview(collected());
 const value=draftReviewInput(state);
 value.proposal={...value.proposal,content:{...value.proposal.content,description:'복구한 새 설명'}};
 const restored=draftReviewReducer(state,{type:'restore',value,revision:1});
 assert.equal(restored.dirty,true);
 assert.equal(restored.latest.proposal.content.description,'첫 줄\n둘째 줄');
});

test('only an explicit keep-my-input choice rebases a conflicted draft and requires saving again',()=>{
 let state=initialDraftReview(collected());
 state=draftReviewReducer(state,{type:'edit',value:{note:'담당자 검토',unresolved:'미정\n',target:'item-1',targetRevision:5}});
 state=draftReviewReducer(state,{type:'receive',draft:collected(2)});
 assert.equal(state.editRevision,1);
 const rebased=draftReviewReducer(state,{type:'rebase'});
 assert.equal(rebased.editRevision,2);
 assert.equal(rebased.note,'담당자 검토');
 assert.equal(rebased.unresolved,'미정\n');
 assert.equal(rebased.dirty,true);
 assert.deepEqual(rebased.proposal.content.references,[{item_id:'approved-criterion',version:7}]);
 const saved=draftReviewReducer(rebased,{type:'saved',draft:collected(3)});
 assert.equal(saved.editRevision,3);
 assert.equal(saved.dirty,false);
 assert.equal(saved.note,'');
});

test('a polling response for a different exact question or answer never enters the current review',()=>{
 const state=initialDraftReview(collected());
 for(const patch of [{id:'draft-other'},{question_id:'question-other'},{answer_id:'answer-other'}]){
  assert.equal(draftReviewReducer(state,{type:'receive',draft:{...collected(3),...patch}}),state);
 }
});

test('approval into existing knowledge pins the selected revision and scope until explicit comparison',()=>{
 const input={...draftReviewInput(initialDraftReview(collected())),target:'item-1',targetRevision:5};
 assert.equal(draftTargetChanged(input,[item()]),false);
 for(const values of [[],[{...item(),revision:6}],[{...item(),kind:'term'}],[{...item(),scope:'common'}]])assert.equal(draftTargetChanged(input,values),true);
 assert.equal(draftTargetChanged({...input,target:''},[]),false);
});

test('answer recovery retains existing viewer answer opportunities without authorizing processed or changed questions',()=>{
 for(const status of ['open','deferred','review'])assert.equal(questionCanAnswer({status,dataset_changed:false}),true);
 for(const status of ['answered','structuring','deleted','unknown'])assert.equal(questionCanAnswer({status,dataset_changed:false}),false);
 assert.equal(questionCanAnswer({status:'open',dataset_changed:true}),false);
 assert.equal(validAnswer('미완성\n답변'),true);
 assert.equal(validAnswer({text:'답변',answers:[]}),false);
 assert.equal(validAnswer('x'.repeat(6001)),false);
});

test('knowledge drafts cannot be recovered under another user, role, workspace, project or target',()=>{
 const value=draftReviewInput(initialDraftReview(collected())),record={schema:1,identity,baseVersion:1,value,savedAt:123,writer:'tab-1'},raw=JSON.stringify(record);
 assert.deepEqual(readLocalDraft(raw,identity,validReviewInput).value,value);
 for(const patch of [{userId:'user-2'},{role:'viewer'},{workspace:'db-1:revision-5'},{workspace:'db-2'},{projectId:4},{projectId:null},{targetId:'draft-2'},{kind:'knowledge-answer'}]){
  const other={...identity,...patch};
  assert.notEqual(draftKey(other),draftKey(identity));
  assert.throws(()=>readLocalDraft(raw,other,validReviewInput));
 }
});

test('a different tab draft is not overwritten by stale knowledge edits',()=>{
 const values=new Map(),storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 const first={schema:1,identity,baseVersion:1,value:draftReviewInput(initialDraftReview(collected())),savedAt:123,writer:'tab-1'};
 const raw=writeLocalDraft(storage,identity,null,first);
 const second={...first,value:{...first.value,note:'다른 창 검토'},writer:'tab-2'};
 writeLocalDraft(storage,identity,raw,second);
 assert.throws(()=>writeLocalDraft(storage,identity,raw,{...first,value:{...first.value,note:'늦은 변경'}}));
 assert.equal(readLocalDraft(storage.getItem(draftKey(identity)),identity,validReviewInput).value.note,'다른 창 검토');
});

test('new question recovery stores draft fields without dataset contents or arbitrary target metadata',()=>{
 const value={...blankQuestionInput(),title:'작성 중\n',reason:'확인 필요',targetKind:'column',columnKey:'["source-1","CODE"]'};
 assert.equal(validNewQuestionInput(value),true);
 assert.equal(validNewQuestionInput({...value,dataset:{rows:[1]}}),false);
 assert.equal(validNewQuestionInput({...value,targetKind:'unrestricted'}),false);
 assert.equal(validNewQuestionInput({title:'누락'}),false);
});

test('restoring an old mapping cannot rebase it onto a different definition version',()=>{
 const original=item(),version=mappingVersion(original);
 assert.equal(mappingCanRebase(version,original),true);
 assert.equal(mappingCanRebase(version,{...original,binding:{...original.binding,revision:4}}),true);
 assert.equal(mappingCanRebase(version,{...original,version:3,revision:6}),false);
 assert.equal(mappingCanRebase('corrupt',original),false);
});
