import assert from 'node:assert/strict';
import test from 'node:test';
import {draftKey,draftValueSafe,readLocalDraft,writeLocalDraft} from '../src/localDraftStorage.ts';
import {validDatasetDraft,validCardDraft,ganttDraftSource} from '../src/editorDraftValues.ts';
import {prepareCardSave,cardConflictSnapshot} from '../src/cardDraftSave.ts';
const identity={userId:'u1',role:'editor',workspace:'db1',projectId:1,kind:'dataset',targetId:'dataset1'};
const value={name:'작성 중',nameDraft:'새 이름 입력 중',editingName:true,spec:{source_id:'s1',columns:[{name:'',expression:''}],filters:[],raw_sql:null},sql:'SELECT unfinished ',editorMode:'sql',autoGroup:false,sqlOrigin:null};
const record={schema:1,identity,baseVersion:3,value,savedAt:1234,writer:'tab1'};
function storage(){const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)}}

test('draft keys separate exact account, role, database, project, editor kind and target, with stable new-item slots',()=>{
 const key=draftKey(identity);
 for(const patch of [{userId:'u2'},{role:'admin'},{workspace:'db2'},{projectId:null},{kind:'card'},{targetId:'dataset2'}])assert.notEqual(draftKey({...identity,...patch}),key);
 assert.equal(draftKey({...identity,targetId:'new'}),draftKey({...identity,targetId:'new'}));
 assert.notEqual(draftKey({...identity,targetId:'a:b',kind:'c'}),draftKey({...identity,targetId:'b',kind:'c:a'}));
});
test('incomplete editor input restores exactly with its original base version and never becomes a validated result',()=>{
 const disk=storage(),raw=writeLocalDraft(disk,identity,null,record),restored=readLocalDraft(raw,identity,validDatasetDraft);
 assert.deepEqual(restored.value,value);assert.equal(restored.baseVersion,3);assert.equal(restored.value.preview,undefined);
 assert.throws(()=>readLocalDraft(raw,{...identity,workspace:'db2'},validDatasetDraft),/형식/);
});
test('a newer local draft cannot be overwritten by an old editor or initial server response',()=>{
 const disk=storage(),first=writeLocalDraft(disk,identity,null,record);
 const newer={...record,writer:'tab2',savedAt:1235,value:{...value,sql:'SELECT newer'}};
 const latest=writeLocalDraft(disk,identity,first,newer);
 assert.throws(()=>writeLocalDraft(disk,identity,first,{...record,value:{...value,sql:'stale'}}),/다른 창/);
 assert.throws(()=>writeLocalDraft(disk,identity,null,record),/다른 창/);
 assert.equal(disk.getItem(draftKey(identity)),latest);
});
test('malformed, wrong-schema and unexpected editor data are rejected without deleting the stored text',()=>{
 const disk=storage();disk.setItem(draftKey(identity),'{broken');
 assert.throws(()=>readLocalDraft(disk.getItem(draftKey(identity)),identity,validDatasetDraft),/읽을 수/);assert.equal(disk.getItem(draftKey(identity)),'{broken');
 for(const invalid of [{...record,schema:2},{...record,value:{...value,query_result:{rows:[]}}},{...record,value:{...value,spec:{source_id:'s1'}}}])assert.throws(()=>readLocalDraft(JSON.stringify(invalid),identity,validDatasetDraft));
});
test('quota failure and oversized draft preserve the last recoverable copy',()=>{
 const disk=storage(),previous=writeLocalDraft(disk,identity,null,record);disk.setItem=()=>{throw new Error('QuotaExceededError')};
 assert.throws(()=>writeLocalDraft(disk,identity,previous,{...record,value:{...value,sql:'next'}}),/저장 공간/);
 assert.equal(disk.getItem(draftKey(identity)),previous);
 assert.throws(()=>writeLocalDraft(disk,identity,previous,{...record,value:{...value,sql:'x'.repeat(500001)}}),/너무|커서/);
});
test('result rows and credentials cannot enter the draft store, including nested values',()=>{
 for(const key of ['rows','query_result','card_result','api_key','LLM_KEY','password','access_token','authorization']){
  assert.equal(draftValueSafe({nested:{[key]:'secret'}}),false);
  assert.throws(()=>writeLocalDraft(storage(),identity,null,{...record,value:{nested:{[key]:'secret'}}}),/인증 정보/);
 }
 assert.equal(draftValueSafe(value),true);
});
const card={id:'c1',title:'내 수정',dataset_id:'d1',dataset_version:2,kind:'bar',renderer:'plotly',aggregate:'count',filters:[],options:{},x:'lot'};
test('card editor drafts preserve unfinished expressions and scripts while excluding query outputs',()=>{
 const edited={card:{...card,editor_mode:'python',python_script:'fig = px.bar(df)'},expressionDraft:' / ('};
 assert.equal(validCardDraft(edited),true);assert.equal(validCardDraft({...edited,preview:{rows:[]}}),false);assert.equal(validCardDraft({card:{id:'c1'},expressionDraft:''}),false);
});
test('corrupt nested editor structures are rejected before pivot, filters or date controls render',()=>{
 for(const spec of [{...value.spec,pivot:'broken'},{...value.spec,filters:[null]},{...value.spec,group_by:[false]},{...value.spec,pivot:{keys:[],outputs:[null]}}])assert.equal(validDatasetDraft({...value,spec}),false);
 assert.equal(validDatasetDraft({...value,sqlOrigin:{form:'{broken',sql:'SELECT x'}}),false);
 for(const patch of [{filters:[null]},{period:{use_dashboard:'yes'}},{gantt:{as_of:'not-a-date'}},{options:{x_label:4}},{value_expression:[{kind:'column'}]}])assert.equal(validCardDraft({card:{...card,...patch},expressionDraft:''}),false);
 const incompletePivot={keys:[],operation_column:'',value_column:'',time_column:'',tie_breaker:'',outputs:[],require_all:true,filters:[]};
 assert.equal(validDatasetDraft({...value,spec:{...value.spec,pivot:incompletePivot}}),true,'incomplete but well-formed editing remains recoverable');
});
test('card save refuses a refreshed board until its new version is explicitly accepted, preserving unrelated cards and layout',()=>{
 const other={...card,id:'other',title:'동료 최신 카드'},board={id:'b1',project_id:1,version:4,cards:[{...card,title:'동료 수정'},other],layout:[{i:'c1',x:6,y:1,w:6,h:5},{i:'other',x:0,y:8,w:6,h:5}]};
 assert.equal(prepareCardSave(board,card,3),null);
 const accepted=prepareCardSave(board,card,4);
 assert.equal(accepted.version,4);assert.equal(accepted.cards[0].title,'내 수정');assert.deepEqual(accepted.cards[1],other);assert.deepEqual(accepted.layout,board.layout);
 const fresh=prepareCardSave(board,{...card,id:'new-id'},4);assert.equal(fresh.cards.length,3);assert.deepEqual(fresh.layout.at(-1),{i:'new-id',x:0,y:13,w:6,h:5});
});
test('a recovered new card compares the restored ID already saved by another tab, not the blank dialog ID',()=>{
 const recovered={...card,id:'restored-A'},blank={...card,id:'blank-B'},remote={...recovered,title:'다른 창이 저장한 카드'},board={id:'b1',project_id:1,version:5,cards:[remote],layout:[]};
 assert.notEqual(recovered.id,blank.id);
 assert.equal(prepareCardSave(board,recovered,4),null);
 assert.deepEqual(cardConflictSnapshot(board,recovered.id),{card:remote,version:5});
});
test('a recovered gantt keeps its missing dataset instead of silently calculating another source',()=>{
 assert.deepEqual(ganttDraftSource({dataset_id:'deleted-source-A',dataset_version:7},[{id:'unrelated-B',version:2}]),{dataset_id:'deleted-source-A',dataset_version:7});
 assert.deepEqual(ganttDraftSource({dataset_id:'source-A',dataset_version:3},[{id:'source-A',version:4}]),{dataset_id:'source-A',dataset_version:3});
});
