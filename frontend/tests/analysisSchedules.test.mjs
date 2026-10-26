import test from 'node:test';
import assert from 'node:assert/strict';
import {canSchedule,compatibleSchedules,scheduleSave} from '../src/analysisScheduleState.ts';

const run={id:'run',revision:4,status:'completed',kind:'trend',request:{kind:'trend',project_id:7,baseline_start:'2026-01-01T00:00:00Z',current_end:'2026-01-10T00:00:00Z'},result:{series:[{status:'stable'}]}};
const preview={source_run_id:'run',source_run_revision:4,workspace_token:'db:2',interval_hours:24,first_run_at:'2099-01-01T00:00:00Z'};
test('only completed business runs with explicit periods and known criteria offer scheduling',()=>{
 assert.equal(canSchedule(run),true);
 for(const status of ['queued','running','cancelling','cancelled','failed','needs_input'])assert.equal(canSchedule({...run,status}),false);
 assert.equal(canSchedule({...run,request:{...run.request,purpose:'validation'}}),false);
 assert.equal(canSchedule({...run,result:{series:[{status:'needs_criteria'}]}}),false);
 assert.equal(!!canSchedule({...run,kind:'statistics',request:{kind:'statistics'}}),false);
});
test('schedule save uses the exact reviewed run, interval, revision and workspace',()=>{
 assert.deepEqual(scheduleSave(preview,run,'db:2',24,' Weekly '),{source_run_id:'run',expected_run_revision:4,workspace_token:'db:2',name:'Weekly',interval_hours:24,first_run_at:preview.first_run_at});
 for(const patch of [{source_run_id:'other'},{source_run_revision:5},{workspace_token:'db:3'},{interval_hours:1},{first_run_at:'bad'},{first_run_at:'2000-01-01T00:00:00Z'}])assert.equal(scheduleSave({...preview,...patch},run,'db:2',24,'A'),null);
 assert.equal(scheduleSave(preview,run,'db:2',24,' '),null);
});
test('approved resumed conditions can only replace manageable schedules in the same kind and project',()=>{
 const base={id:'yes',kind:'trend',project_id:7,can_manage:true};
 const items=[base,{...base,id:'common',project_id:null},{...base,id:'split',kind:'split'},{...base,id:'other',project_id:8},{...base,id:'forbidden',can_manage:false}];
 assert.deepEqual(compatibleSchedules(items,run).map(s=>s.id),['yes']);
});
