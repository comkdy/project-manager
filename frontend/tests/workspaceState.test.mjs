import assert from 'node:assert/strict';
import test from 'node:test';
import {setImmediate} from 'node:timers/promises';
import {QueryClient,QueryObserver} from '@tanstack/react-query';
import {databaseStatusSignal} from '../src/databaseStatus.ts';
import {selectedAnalysisRun} from '../src/analysisSelection.ts';

test('connection status shows a failed check after a successful cached response and recovers',async()=>{
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}});
 let fail=false;
 const observer=new QueryObserver(client,{queryKey:['database-status'],queryFn:async()=>{
  if(fail)throw new Error('status endpoint unavailable');
  return {status:'connected',database:'production'};
 }});
 const stop=observer.subscribe(()=>{});
 try{
  await setImmediate();
  assert.equal(databaseStatusSignal(observer.getCurrentResult()).state,'connected');
  fail=true;await observer.refetch();
  const failed=observer.getCurrentResult();
  assert.equal(failed.data.status,'connected','React Query retains the last response');
  for(const active of [false,true]){
   const signal=databaseStatusSignal(failed,active);
   assert.equal(signal.state,'error');
   assert.match(signal.label,/확인 실패/);
   assert.match(signal.label,/마지막 확인/);
   assert.doesNotMatch(signal.label,/연결됨/);
  }
  fail=false;await observer.refetch();
  assert.equal(databaseStatusSignal(observer.getCurrentResult()).state,'connected');
 }finally{stop();client.clear()}
});

test('initial loading and unconfigured source have distinct connection states',()=>{
 const query={data:undefined,isError:false,isFetching:true,dataUpdatedAt:0};
 assert.equal(databaseStatusSignal(query).state,'checking');
 assert.equal(databaseStatusSignal({...query,isFetching:false,data:{status:'not_configured'}}).label,'DB 미연결');
 const failure=databaseStatusSignal({...query,isFetching:false,isError:true});
 assert.match(failure.label,/확인 실패/);
 assert.doesNotMatch(failure.label,/마지막 확인/);
});

test('analysis navigation cannot reuse another workspace or account’s selection',()=>{
 const entry={workspace:'database-A',userId:'user-A',runId:'analysis-A'};
 assert.equal(selectedAnalysisRun(entry,'database-A','user-A'),'analysis-A');
 assert.equal(selectedAnalysisRun(entry,'database-B','user-A'),null);
 assert.equal(selectedAnalysisRun(entry,'database-A','user-B'),null);
 assert.equal(selectedAnalysisRun(entry,'database-A',undefined),null);
 assert.equal(selectedAnalysisRun(null,'database-A','user-A'),null);
 const next={workspace:'database-B',userId:'user-B',runId:'analysis-B'};
 assert.equal(selectedAnalysisRun(next,'database-B','user-B'),'analysis-B');
});
