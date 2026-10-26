import type {ScheduleComparison,ScheduleHistory,SeriesComparison} from './analysisHistory.generated';

export function historyMatches(data:ScheduleHistory|ScheduleComparison|undefined,workspace:string,scheduleId:string,runId?:string){
 return !!data&&data.workspace_token===workspace&&data.schedule_id===scheduleId&&(!runId||'current' in data&&data.current.id===runId);
}
export const comparisonChangeNames:Record<SeriesComparison['change'],string>={
 added:'계열 추가',removed:'계열 누락',state:'판정 변경',direction:'변화 방향 변경',
 values:'수치만 변동',unchanged:'동일',not_comparable:'조건 확인 필요',
};
export const evidenceStatusNames:Record<string,string>={changed:'변화 감지',stable:'기준 미만',insufficient_data:'표본 부족',needs_criteria:'기준 확인 필요'};
