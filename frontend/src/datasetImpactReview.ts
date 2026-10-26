import type {DatasetSpec,QueryContext} from './types.ts';
import type {DatasetImpact} from './datasetImpact.generated.ts';

export interface DatasetImpactDraft {
 datasetId:string;viewedVersion:number;name:string;spec:DatasetSpec|null;
 context:QueryContext;expectedVersion:number;editor:unknown;
}
export interface DatasetImpactSnapshot {
 signature:string;datasetId:string;viewedVersion:number;
 body:{name:string;spec:DatasetSpec;project_id:number|null;expected_version:number};
}
export const datasetImpactSignature=(draft:DatasetImpactDraft)=>JSON.stringify(draft);
export function datasetImpactSnapshot(draft:DatasetImpactDraft):DatasetImpactSnapshot {
 if(!draft.spec)throw new Error('조회 결과를 먼저 미리보기해 주세요.');
 return {signature:datasetImpactSignature(draft),datasetId:draft.datasetId,viewedVersion:draft.viewedVersion,
  body:{name:draft.name.trim(),spec:structuredClone(draft.spec),project_id:draft.context.project_id??null,expected_version:draft.expectedVersion}};
}
export function impactMatchesSnapshot(snapshot:DatasetImpactSnapshot,result:DatasetImpact):boolean {
 return result.dataset_id===snapshot.datasetId&&result.current_version===snapshot.body.expected_version&&
  result.proposed_version===snapshot.body.expected_version+1&&result.project_id===snapshot.body.project_id&&result.validation_mode==='static';
}
export type DatasetImpactRequest=(snapshot:DatasetImpactSnapshot,signal:AbortSignal)=>Promise<DatasetImpact>;
export function datasetImpactQueryOptions(snapshot:DatasetImpactSnapshot,request:DatasetImpactRequest){
 return {queryKey:['dataset-impact',snapshot.datasetId,snapshot.signature] as const,
  queryFn:async({signal}:{signal:AbortSignal})=>{
   const result=await request(snapshot,signal);
   if(!impactMatchesSnapshot(snapshot,result))throw new Error('변경 영향의 기준 버전이 달라졌습니다. 최신 버전을 확인한 뒤 다시 검토해 주세요.');
   return result;
  },staleTime:0,gcTime:0,retry:false,refetchOnWindowFocus:false};
}
export function canSaveDatasetImpact(snapshot:DatasetImpactSnapshot,query:{data?:DatasetImpact;isSuccess:boolean;isFetching:boolean;isError:boolean},acknowledged:boolean):boolean {
 return query.isSuccess&&!query.isFetching&&!query.isError&&!!query.data&&impactMatchesSnapshot(snapshot,query.data)&&
  (!query.data.cards.some(card=>card.status==='incompatible')||acknowledged);
}
