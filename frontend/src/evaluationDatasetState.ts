import type {Proposal,Source} from './types';

export function preferredSetupEntry(run?:{id:string;project_id:number|null;entry:number}|null,dataset?:{id:string;entry:number}|null){
 if(dataset&&(!run||dataset.entry>run.entry))return {kind:'dataset' as const,value:dataset};
 return run?{kind:'analysis' as const,value:run}:null;
}

export interface DatasetRecommendation {
 id:string;revision:number;workspace_token:string;project_id:number|null;case_ref:{id:string;version:number};case_name:string;
 status:'running'|'waiting'|'ready'|'applied'|'error';summary?:string;message?:string;limitations?:string[];
 dataset_id:string|null;run_id:string|null;dataset?:{id:string;name:string;version:number;project_id:number|null};
 questions:{id:string;title:string;status:string}[];evidence?:{reference:string;quote:string}[];alignment_basis?:{reference:string;quote:string};
 proposal?:Proposal;catalog?:Source[];
}

export function recommendedDatasetPrepare(value:DatasetRecommendation,workspace:string){
 if(value.workspace_token!==workspace||!['ready','applied'].includes(value.status)||!value.dataset_id||value.questions.some(q=>q.status!=='answered'))throw new Error('현재 DB의 데이터 추천과 승인된 연결을 먼저 확인하세요.');
 return {explicit_case:true,dataset_recommendation_id:value.id,case_ref:value.case_ref,suggestion_id:`${value.case_ref.id}:recheck`,
  search:{query:value.case_name,dataset_id:value.dataset_id,project_id:value.project_id}};
}
