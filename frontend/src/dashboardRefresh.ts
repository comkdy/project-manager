import type {QueryClient} from '@tanstack/react-query';
import type {CardSpec,ChartResult,QueryContext} from './types.ts';

export const DASHBOARD_REFRESH_MS=60_000;
export const dashboardRefreshOptions={
 staleTime:DASHBOARD_REFRESH_MS,
 refetchInterval:DASHBOARD_REFRESH_MS,
 refetchIntervalInBackground:false,
 refetchOnWindowFocus:true,
};

export type ChartRequest=(body:{card:CardSpec;context:QueryContext},signal?:AbortSignal)=>Promise<ChartResult>;

export function chartQueryOptions(card:CardSpec,context:QueryContext,request:ChartRequest){
 return {
  queryKey:['chart',card,context] as const,
  queryFn:({signal}:{signal:AbortSignal})=>request({card,context:{...context,refresh:false}},signal),
 };
}

export async function refreshDashboardCharts(client:QueryClient,cards:CardSpec[],context:QueryContext,request:ChartRequest){
 // Use the same query entry so existing charts remain visible and requests
 // already in flight are shared. Only this explicit action bypasses the TTL.
 const targets=cards.flatMap(card=>{
  if(card.kind!=='gantt')return [{card,context}];
  const active=client.getQueryCache().findAll({queryKey:['chart'],type:'active'}).filter(q=>(q.queryKey[1] as CardSpec)?.id===card.id&&(q.queryKey[2] as QueryContext)?.project_id===context.project_id);
  return active.length?active.map(q=>({card,context:q.queryKey[2] as QueryContext})):[{card,context}];
 });
 await Promise.all(targets.map(({card,context:target})=>client.fetchQuery({
  ...chartQueryOptions(card,target,request),
  queryFn:({signal})=>request({card,context:{...target,refresh:true}},signal),
  staleTime:0,
 })));
}
