import test from 'node:test';
import assert from 'node:assert/strict';
import {activityMatches,activitySummary,cardActivity,validTimestamp} from '../src/workspaceActivity.ts';
const card={id:'one',title:'측정값',dataset_id:'shared',dataset_version:1,kind:'line',options:{},filters:[]};
const context={project_id:7,period_start:{year:2026,week:40}};
const entry=(c=card,ctx=context,meta={queried_at:'2026-10-23T01:00:00Z',cached:true,dataset_version:2},state={})=>({queryKey:['chart',c,ctx],state:{data:{rows:[],meta},dataUpdatedAt:100,status:'success',fetchStatus:'idle',...state}});
test('activity list rejects another database revision and project response',()=>{
 const data={workspace_token:'db:2',project_id:7};
 assert.equal(activityMatches(data,'db:2',7),true);
 assert.equal(activityMatches(data,'db:3',7),false);
 assert.equal(activityMatches(data,'db:2',null),false);
});
test('summary counts displayed states without treating questions as running work',()=>{
 assert.deepEqual(activitySummary(['queued','running','needs_input','needs_confirmation','failed','completed'].map(status=>({status}))),{active:2,attention:3,completed:1});
});
test('chart activity uses execution timestamp and version, not transport arrival time',()=>{
 const result=cardActivity([card],context,[entry()])[0];
 assert.equal(result.queriedAt,'2026-10-23T01:00:00Z');assert.equal(result.datasetVersion,2);assert.equal(result.cached,true);assert.equal(result.sourceAt,null);
});
test('different project, period, card definition and preview card never contaminate freshness',()=>{
 const queries=[entry(card,{...context,project_id:8}),entry(card,{...context,period_start:{year:2026,week:41}}),entry({...card,title:'임시 편집'}),entry({...card,id:'other'})];
 assert.equal(cardActivity([card],context,queries)[0].state,'not_loaded');
});
test('permission failure suppresses old data while a failed refresh explicitly retains prior timestamp',()=>{
 const denied=cardActivity([card],context,[entry(card,context,undefined,{error:{status:403}})])[0];
 assert.equal(denied.state,'failed');assert.equal(denied.queriedAt,null);
 const failed=cardActivity([card],context,[entry(card,context,undefined,{error:{status:503}})])[0];
 assert.equal(failed.state,'failed');assert.equal(failed.queriedAt,'2026-10-23T01:00:00Z');
});
test('gantt reports its snapshot separately and makes narrowed last-view scope explicit',()=>{
 const gantt={...card,kind:'gantt'};
 const result=cardActivity([gantt],context,[entry(gantt,{...context,gantt_page:2,gantt_search:'LOT'},{queried_at:'2026-10-23T01:00:00Z',as_of:'2026-10-20T00:00:00Z'})])[0];
 assert.equal(result.sourceAt,'2026-10-20T00:00:00Z');assert.equal(result.limitedGanttScope,true);
});
test('invalid or missing timestamp is unknown; a loading query never reports completed',()=>{
 assert.equal(validTimestamp('not-a-date'),null);assert.equal(validTimestamp(null),null);
 const result=cardActivity([card],context,[entry(card,context,{queried_at:'invalid'},{fetchStatus:'fetching'})])[0];
 assert.equal(result.state,'fetching');assert.equal(result.queriedAt,null);
});

test('a later failed or loading gantt page overrides an older successful page',()=>{
 const gantt={...card,kind:'gantt'},old=entry(gantt,{...context,gantt_page:0});
 const denied=entry(gantt,{...context,gantt_page:1},undefined,{data:undefined,dataUpdatedAt:0,errorUpdatedAt:200,error:{status:403}});
 const failure=cardActivity([gantt],context,[old,denied])[0];
 assert.equal(failure.state,'failed');assert.equal(failure.queriedAt,null);
 const loading=entry(gantt,{...context,gantt_page:2},undefined,{data:undefined,dataUpdatedAt:0,fetchStatus:'fetching'});
 assert.equal(cardActivity([gantt],context,[old,loading])[0].state,'fetching');
});
