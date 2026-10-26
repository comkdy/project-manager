import type {DatasetSpec,Proposal} from './types';

// A handwritten SQL change is no longer described by the generated pivot recipe.
export function proposalSqlSpec(spec:DatasetSpec,sql:string):DatasetSpec{
 return {...spec,raw_sql:sql,...(sql!==spec.raw_sql?{pivot:null}:{})};
}

// Never send rows or model progress fields to the persistence endpoint.
export function proposalDefinition(p:Proposal){
 return {kind:p.kind,explanation:p.explanation,...(p.evaluation_dataset_recommendation_id?{evaluation_dataset_recommendation_id:p.evaluation_dataset_recommendation_id}:{}),...(p.knowledge_context_id?{knowledge_context_id:p.knowledge_context_id}:{}),...(p.dataset?{dataset:p.dataset}:{}),...(p.card?{card:p.card}:{})};
}
export function proposalTitle(p:Proposal){
 return p.kind==='query'?'조회 SQL':p.kind==='dataset'?'데이터셋 제안':p.dataset?'데이터셋 + 카드 제안':'카드 제안';
}
export function historyContent(text:string,p?:Proposal){
 return (p?`${text}\n제안 내용(아직 저장 여부는 별도 확인):\n${JSON.stringify(proposalDefinition(p))}`:text).slice(0,6000);
}
