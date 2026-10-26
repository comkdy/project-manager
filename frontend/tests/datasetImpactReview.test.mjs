import assert from 'node:assert/strict';
import test from 'node:test';
import {setImmediate} from 'node:timers/promises';
import {QueryClient,QueryObserver} from '@tanstack/react-query';
import {canSaveDatasetImpact,datasetImpactQueryOptions,datasetImpactSignature,datasetImpactSnapshot,impactMatchesSnapshot} from '../src/datasetImpactReview.ts';

const spec={source_id:'work',columns:[{name:'amount',expression:'amount'}],filters:[],group_by:[]};
const draft={datasetId:'d1',viewedVersion:4,name:'작업 실적',spec,context:{project_id:7,period_start:{year:2026,week:40}},expectedVersion:4,editor:{mode:'form',spec}};
const snapshot=()=>datasetImpactSnapshot(structuredClone(draft));
const result=(value=snapshot(),cards=[])=>({dataset_id:value.datasetId,current_version:value.body.expected_version,proposed_version:value.body.expected_version+1,project_id:value.body.project_id,validation_mode:'static',column_changes:[],cards,notes:[]});
const success=data=>({data,isSuccess:true,isFetching:false,isError:false});
const flush=()=>setImmediate();
function watch(t,value,request){
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}});
 const observer=new QueryObserver(client,datasetImpactQueryOptions(value,request));
 const unsubscribe=observer.subscribe(()=>{});
 t.after(()=>{unsubscribe();client.clear()});
 return {client,observer};
}

test('any input, scope, history or latest version change invalidates the reviewed snapshot',()=>{
 const original=snapshot();
 for(const changed of [
  {...draft,name:'수정한 이름'},
  {...draft,spec:{...spec,columns:[]}},
  {...draft,context:{...draft.context,project_id:8}},
  {...draft,context:{...draft.context,period_start:{year:2026,week:41}}},
  {...draft,expectedVersion:5},
  {...draft,viewedVersion:2},
  {...draft,editor:{mode:'sql',sql:'select amount from work'}},
  {...draft,spec:null},
 ])assert.notEqual(datasetImpactSignature(changed),original.signature);
});

test('snapshot keeps exactly the reviewed definition even if the draft changes later',()=>{
 const editable=structuredClone(draft),reviewed=datasetImpactSnapshot(editable);
 editable.spec.columns[0].name='renamed';
 assert.equal(reviewed.body.spec.columns[0].name,'amount');
 assert.equal(reviewed.body.expected_version,4);
 assert.equal(reviewed.body.project_id,7);
 assert.throws(()=>datasetImpactSnapshot({...draft,spec:null}),/미리보기/);
});

test('restoring an older definition compares and saves against the current latest version',()=>{
 const restored=datasetImpactSnapshot({...draft,viewedVersion:1});
 assert.equal(restored.viewedVersion,1);
 assert.equal(restored.body.expected_version,4);
 assert.equal(impactMatchesSnapshot(restored,result(restored)),true);
 assert.equal(impactMatchesSnapshot(restored,{...result(restored),current_version:1,proposed_version:2}),false);
});

test('mismatched dataset, scope, version or validation mode cannot authorize a save',()=>{
 const reviewed=snapshot(),valid=result(reviewed);
 for(const invalid of [
  {...valid,dataset_id:'d2'},
  {...valid,project_id:null},
  {...valid,current_version:3},
  {...valid,proposed_version:6},
  {...valid,validation_mode:'executed'},
 ])assert.equal(canSaveDatasetImpact(reviewed,success(invalid),true),false);
});

test('only a completed successful impact check permits saving and failures fail closed',()=>{
 const reviewed=snapshot(),valid=result(reviewed),ready=success(valid);
 assert.equal(canSaveDatasetImpact(reviewed,ready,false),true);
 for(const pendingOrFailed of [
  {...ready,data:undefined,isSuccess:false},
  {...ready,isFetching:true},
  {...ready,isSuccess:false,isError:true},
  {...ready,isError:true},
 ])assert.equal(canSaveDatasetImpact(reviewed,pendingOrFailed,true),false);
});

test('incompatible cards require conscious acknowledgement while review-only cards do not block',()=>{
 const reviewed=snapshot();
 const incompatible=success(result(reviewed,[{status:'incompatible',issues:['missing amount']}]));
 assert.equal(canSaveDatasetImpact(reviewed,incompatible,false),false);
 assert.equal(canSaveDatasetImpact(reviewed,incompatible,true),true);
 assert.equal(canSaveDatasetImpact(reviewed,success(result(reviewed,[{status:'review',issues:['Python requires execution']}])) ,false),true);
});

test('impact request errors and failed refetches cannot reuse a previous successful result',async t=>{
 const reviewed=snapshot();let calls=0;
 const {observer}=watch(t,reviewed,async()=>{if(++calls>1)throw new Error('unavailable');return result(reviewed)});
 await flush();assert.equal(canSaveDatasetImpact(reviewed,observer.getCurrentResult(),true),true);
 await observer.refetch();
 assert.ok(observer.getCurrentResult().data,'query retains old data after a failed refetch');
 assert.equal(observer.getCurrentResult().isError,true);
 assert.equal(canSaveDatasetImpact(reviewed,observer.getCurrentResult(),true),false);
});

test('out-of-order responses after changing the reviewed definition never replace the new review',async t=>{
 const first=snapshot(),next=datasetImpactSnapshot({...draft,name:'변경한 이름'});
 let finishFirst,firstSignal;
 const {observer}=watch(t,first,async(value,signal)=>{firstSignal=signal;await new Promise(resolve=>finishFirst=resolve);return result(value)});
 await flush();assert.equal(canSaveDatasetImpact(first,observer.getCurrentResult(),true),false);
 observer.setOptions(datasetImpactQueryOptions(next,async value=>result(value,[{status:'incompatible',card_id:'new-card'}])));
 await flush();assert.equal(firstSignal.aborted,true);
 assert.equal(observer.getCurrentResult().data.cards[0].card_id,'new-card');
 finishFirst();await flush();
 assert.equal(observer.getCurrentResult().data.cards[0].card_id,'new-card');
 assert.equal(canSaveDatasetImpact(next,observer.getCurrentResult(),false),false);
});

test('server result with a different baseline is treated as an error, never a usable review',async t=>{
 const reviewed=snapshot();
 const {observer}=watch(t,reviewed,async()=>({...result(reviewed),current_version:5,proposed_version:6}));
 await flush();
 assert.equal(observer.getCurrentResult().isError,true);
 assert.match(observer.getCurrentResult().error.message,/기준 버전/);
 assert.equal(canSaveDatasetImpact(reviewed,observer.getCurrentResult(),true),false);
});
