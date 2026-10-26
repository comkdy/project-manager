import type {AnalysisRun} from './analyticsTypes';
import type {SchedulePreview,ScheduleSave,ScheduleView} from './analysisSchedules.generated';

export const scheduleIntervals=[{value:1,label:'1시간마다'},{value:6,label:'6시간마다'},{value:24,label:'24시간마다'},{value:168,label:'7일마다'}] as const;
export function canSchedule(run:AnalysisRun){
 return run.status==='completed'&&!!run.result&&run.request.purpose!=='validation'&&
  !!(run.kind==='trend'?run.request.current_end&&run.request.baseline_start:run.request.start&&run.request.end)&&
  !run.result.series.some(s=>s.status==='needs_criteria');
}
export function compatibleSchedules(items:ScheduleView[],run:AnalysisRun){return items.filter(s=>s.can_manage&&s.kind===run.kind&&s.project_id===(run.request.project_id??null))}
export function scheduleSave(preview:SchedulePreview,run:AnalysisRun,workspace:string,interval:1|6|24|168,name:string):ScheduleSave|null{
 if(!canSchedule(run)||preview.interval_hours!==interval||preview.workspace_token!==workspace||preview.source_run_id!==run.id||preview.source_run_revision!==run.revision||!name.trim()||!Number.isFinite(Date.parse(preview.first_run_at))||Date.parse(preview.first_run_at)<=Date.now())return null;
 return {source_run_id:run.id,interval_hours:interval,workspace_token:workspace,name:name.trim(),expected_run_revision:preview.source_run_revision,first_run_at:preview.first_run_at};
}
