import assert from 'node:assert/strict';
import test from 'node:test';
import {setImmediate} from 'node:timers/promises';
import {chartQueryOptions,dashboardRefreshOptions,refreshDashboardCharts} from '../src/dashboardRefresh.ts';
import {displayedChartVersion,receivedChartVersion} from '../src/chartVersion.ts';

// QueryObserver must run its browser timers; no UI or source database is used.
globalThis.window={addEventListener(){},removeEventListener(){}};
const {QueryClient,QueryObserver,focusManager}=await import('@tanstack/react-query');
const card={id:'c1',dataset_id:'d1',kind:'kpi'};
const context={project_id:1,period_start:{year:2026,week:40}};
const flush=()=>setImmediate();

function watch(t,request){
 t.mock.timers.enable({apis:['Date','setTimeout','setInterval'],now:1_000_000});
 focusManager.setFocused(true);
 const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:Infinity}}});
 client.mount();
 const observer=new QueryObserver(client,{...chartQueryOptions(card,context,request),...dashboardRefreshOptions});
 const unsubscribe=observer.subscribe(()=>{});
 t.after(()=>{unsubscribe();client.unmount();client.clear();focusManager.setFocused(true)});
 return {client,observer,unsubscribe};
}

test('refreshes at 60 seconds, retains data while fetching and stops after leaving the dashboard',async t=>{
 let calls=0,finish;
 const {observer,unsubscribe}=watch(t,async()=>{
  calls++;
  if(calls===2)await new Promise(resolve=>finish=resolve);
  return {rows:[{value:calls}]};
 });
 await flush();assert.equal(calls,1);
 t.mock.timers.tick(59_999);await flush();assert.equal(calls,1);
 t.mock.timers.tick(1);await flush();assert.equal(calls,2);
 assert.deepEqual(observer.getCurrentResult().data.rows,[{value:1}]);
 // A slow request cannot overlap itself at the next tick.
 t.mock.timers.tick(60_000);await flush();assert.equal(calls,2);
 finish();await flush();assert.equal(observer.getCurrentResult().data.rows[0].value,2);
 unsubscribe();t.mock.timers.tick(180_000);await flush();assert.equal(calls,2);
});

test('pauses in a hidden tab, refreshes on return, and recovers after a failed refresh',async t=>{
 let calls=0;
 const {observer}=watch(t,async()=>{
  calls++;
  if(calls===2)throw new Error('temporarily unavailable');
  return {rows:[{value:calls}]};
 });
 await flush();focusManager.setFocused(false);
 t.mock.timers.tick(120_000);await flush();assert.equal(calls,1);
 focusManager.setFocused(true);await flush();assert.equal(calls,2);
 assert.equal(observer.getCurrentResult().data.rows[0].value,1);
 t.mock.timers.tick(60_000);await flush();assert.equal(calls,3);
 assert.equal(observer.getCurrentResult().data.rows[0].value,3);
 assert.equal(observer.getCurrentResult().error,null);
});

test('manual refresh bypasses the server cache once; later polling shares it again',async t=>{
 const requests=[];
 const request=async body=>{requests.push(body);return {rows:[{value:requests.length}]}};
 const {client,observer}=watch(t,request);
 await flush();
 await refreshDashboardCharts(client,[card],context,request);
 assert.deepEqual(requests.map(r=>r.context.refresh),[false,true]);
 assert.equal(client.getQueryCache().getAll().length,1);
 assert.equal(observer.getCurrentResult().data.rows[0].value,2);
 t.mock.timers.tick(60_000);await flush();
 assert.deepEqual(requests.map(r=>r.context.refresh),[false,true,false]);
 assert.deepEqual(requests[2].context.period_start,context.period_start);
});

test('manual refresh and another observer share an in-flight query; different periods remain isolated',async t=>{
 let calls=0,finish;
 const request=async()=>{calls++;await new Promise(resolve=>finish=resolve);return {rows:[]}};
 const {client}=watch(t,request);
 const duplicate=new QueryObserver(client,{...chartQueryOptions(card,context,request),...dashboardRefreshOptions});
 const unsubscribe=duplicate.subscribe(()=>{});t.after(unsubscribe);
 const manual=refreshDashboardCharts(client,[card],context,request);
 await flush();assert.equal(calls,1);
 finish();await manual;
 await client.fetchQuery(chartQueryOptions(card,{...context,period_start:{year:2026,week:41}},async()=>{calls++;return {rows:[]}}));
 assert.equal(calls,2);assert.equal(client.getQueryCache().getAll().length,2);
});

test('automatic refresh displays the actual dataset version without replacing board metadata',async t=>{
 let version=1,received;
 const savedCard={...card,dataset_version:1};
 const board={version:7,cards:[savedCard],layout:[{i:'c1',x:0,y:0,w:6,h:5}]};
 const {client,observer}=watch(t,async()=>({rows:[{value:version}],meta:{dataset_version:version}}));
 client.setQueryData(['dashboard',1],board);
 const stop=observer.subscribe(result=>{if(result.data)received=receivedChartVersion(savedCard,context,result.data)});
 t.after(stop);
 await flush();
 assert.equal(displayedChartVersion(savedCard,context,received),1);
 version=2;t.mock.timers.tick(60_000);await flush();
 assert.equal(displayedChartVersion(savedCard,context,received),2);
 assert.equal(client.getQueryData(['dashboard',1]),board,'chart metadata must not replace a board being edited');
});

test('version observations do not leak across a card’s dataset or period change',()=>{
 const original={...card,dataset_version:1};
 const observed=receivedChartVersion(original,context,{meta:{dataset_version:3}});
 assert.equal(displayedChartVersion(original,context,observed),3);
 assert.equal(displayedChartVersion({...original,dataset_id:'another',dataset_version:2},context,observed),2);
 assert.equal(displayedChartVersion(original,{...context,period_start:{year:2026,week:41}},observed),1);
 assert.equal(receivedChartVersion(original,context,{meta:{dataset_version:null}}).version,1);
});
