import type {EvaluationClaim,EvaluationExecution} from './evaluationRelationships';
export interface EvaluationCase {
 id:string;version:number;name:string;description:string;
 details:{operation:string;recipe_changes:string;inline_observations:string;epm_observations:string;analysis_result?:string;conditions:string;evidence:string;limitations:string;claims?:EvaluationClaim[]};
 dataset:{id:string;name:string;version:number;project_id:number|null}|null;case_context?:{project_id:number|null}|null;
 match_reasons:string[];limitations:string[];
 execution_evidence?:EvaluationExecution;
}
export interface EvaluationSuggestion {
 id:string;title:string;reason:string;question:string;action:string;case_ref:{id:string;version:number};
}
export interface EvaluationNarrativeCitation {
 case_ref:{id:string;version:number};case_name:string;field:string;label:string;quote:string;
}
export interface EvaluationNarrativeStatement {text:string;citations:EvaluationNarrativeCitation[]}
export type EvaluationNarrative = {status:'ready';notice:string;findings:EvaluationNarrativeStatement[];next_checks:EvaluationNarrativeStatement[];caveats:EvaluationNarrativeStatement[]}|{status:'unavailable';message:string};
export interface ChatEvaluationCases {
 kind:'evaluation_cases';status:'ready'|'empty'|'unavailable';explanation:string;
 cases:EvaluationCase[];suggestions:EvaluationSuggestion[];warnings:string[];
 context?:{project_id?:number|null;dataset_id?:string|null};
 search?:{query:string;project_id?:number|null;dataset_id?:string|null;card?:Record<string,unknown>|null;period_start?:unknown};
 analyses?:{id:string;status:string;explanation?:string;available?:boolean}[];
 narrative?:EvaluationNarrative;
}
/** Exact display evidence reconstructed from the server's saved-SQL identities. */
export function measurementIdentityEvidence(item:EvaluationCase):string|undefined{
 const labels=item.execution_evidence?.measurement_items?.labels;
 if(!labels||!Object.keys(labels).length)return undefined;
 return '원본 실행에 보존된 데이터셋 SQL의 항목 식별: '+Object.entries(labels).map(([column,label])=>`${column} = ${label}`).join('; ')+'. Inline은 operation+parameter 단위이며 같은 parameter를 다른 공정까지 일반화하지 않습니다.';
}
/** Explanations and their citations disappear together when evidence is unavailable. */
export function evaluationNarrative(value:ChatEvaluationCases):Extract<EvaluationNarrative,{status:'ready'}>|undefined{
 const narrative=value.narrative;
 if(value.status!=='ready'||narrative?.status!=='ready')return undefined;
 const statements=[...narrative.findings,...narrative.next_checks,...narrative.caveats];
 if(!statements.length||!statements.every(s=>s.citations.length&&s.citations.every(c=>{
  const item=value.cases.find(v=>v.id===c.case_ref.id&&v.version===c.case_ref.version);
  if(!item||item.execution_evidence?.unavailable)return false;
  const text=c.field==='measurement_identity'?measurementIdentityEvidence(item):c.field==='description'?item.description:item.details[c.field as keyof EvaluationCase['details']];
  return !!c.quote&&typeof text==='string'&&text.includes(c.quote);
 })))return undefined;
 return narrative;
}
/** A saved suggestion can be used only with its currently available case version. */
export function evaluationSuggestions(value:ChatEvaluationCases):EvaluationSuggestion[]{
 if(value.status!=='ready')return [];
 const available=new Set(value.cases.map(item=>JSON.stringify([item.id,item.version])));
 return value.suggestions.filter(s=>available.has(JSON.stringify([s.case_ref.id,s.case_ref.version])));
}
