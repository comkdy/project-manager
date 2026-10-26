import {hashKey} from '@tanstack/react-query';
import type {CardSpec,ChartResult,QueryContext} from './types.ts';
import type {WorkspaceActivity,WorkspaceActivityItem} from './workspaceActivity.generated.ts';

export const activityStatusNames:Record<WorkspaceActivityItem['status'],string>={queued:'대기 중',running:'진행 중',cancelling:'취소 요청됨',cancelled:'취소 완료',needs_input:'기준 확인 필요',needs_confirmation:'실행 전 확인',completed:'완료',failed:'실패',unknown:'상태 확인 필요'};
export const activityKindNames:Record<WorkspaceActivityItem['kind'],string>={analysis_run:'분석',analysis_proposal:'AI 분석 조건',knowledge_collection:'지식 수집'};
export const requiresAttention=(item:WorkspaceActivityItem)=>['needs_input','needs_confirmation','failed','unknown'].includes(item.status);
export function activitySummary(items:WorkspaceActivityItem[]){return {active:items.filter(i=>['queued','running','cancelling'].includes(i.status)).length,attention:items.filter(requiresAttention).length,completed:items.filter(i=>i.status==='completed').length};}
export function activityMatches(value:WorkspaceActivity|undefined,workspace:string,projectId:number|null){return !!value&&value.workspace_token===workspace&&(value.project_id??null)===projectId;}
export function validTimestamp(value:unknown):string|null{return typeof value==='string'&&value.trim()&&Number.isFinite(Date.parse(value))?value:null;}

type CacheEntry={queryKey:readonly unknown[];state:{data?:unknown;dataUpdatedAt:number;errorUpdatedAt?:number;error?:unknown;fetchStatus?:string;status?:string}};
export interface CardActivity {id:string;title:string;state:'not_loaded'|'fetching'|'failed'|'available';queriedAt:string|null;sourceAt:string|null;cached:boolean;datasetVersion:number|null;limitedGanttScope:boolean}
function baseContext(context:QueryContext):QueryContext{
 const {gantt_page:page,gantt_search:search,gantt_detail_lot:detail,gantt_lot_cd:lot,...base}=context;
 return {...base,project_id:context.project_id??null};
}
/** Only read existing cache entries matching the saved card and current period/scope. */
export function cardActivity(cards:CardSpec[],context:QueryContext,entries:CacheEntry[]):CardActivity[]{
 return cards.map(card=>{
  const candidates=entries.filter(entry=>{
   if(entry.queryKey[0]!=='chart'||!entry.queryKey[1]||!entry.queryKey[2])return false;
   const cachedCard=entry.queryKey[1] as CardSpec,cachedContext=entry.queryKey[2] as QueryContext;
   return hashKey([cachedCard])===hashKey([card])&&hashKey([baseContext(cachedContext)])===hashKey([baseContext(context)]);
  }).sort((a,b)=>Number(b.state.fetchStatus==='fetching')-Number(a.state.fetchStatus==='fetching')||
   Math.max(b.state.dataUpdatedAt,b.state.errorUpdatedAt??0)-Math.max(a.state.dataUpdatedAt,a.state.errorUpdatedAt??0));
  const latest=candidates[0],error=latest?.state.error as {status?:number}|undefined;
  const unavailable=typeof error?.status==='number'&&error.status>=400&&error.status<500;
  const result=!unavailable?latest?.state.data as ChartResult|undefined:undefined;
  const meta=result?.meta;
  const queriedAt=validTimestamp(meta?.queried_at),cachedContext=latest?.queryKey[2] as QueryContext|undefined;
  return {id:card.id,title:card.title,state:latest?.state.fetchStatus==='fetching'?'fetching':latest?.state.error?'failed':meta?'available':'not_loaded',
   queriedAt,sourceAt:card.kind==='gantt'?validTimestamp(meta?.as_of):null,cached:meta?.cached===true,
   datasetVersion:typeof meta?.dataset_version==='number'?meta.dataset_version:null,
   limitedGanttScope:card.kind==='gantt'&&!!(cachedContext?.gantt_page||cachedContext?.gantt_search||cachedContext?.gantt_detail_lot||cachedContext?.gantt_lot_cd)};
 });
}
