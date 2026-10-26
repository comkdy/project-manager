export interface ReportedValue {value:string;answer_id:string;quote:string}
export interface EvaluationClaim {
 source_term:string;target_term:string;relationship:'reported_influence'|'co_movement'|'hypothesis';
 answer_id:string;quote:string;direction:ReportedValue|null;magnitude:ReportedValue|null;
}
export interface ComputedFindings {
 method:string;causal_effect:null;notice:string;
 observed_differences:{column:string;item:string;baseline:string;comparison:string;delta:number|null;percent_change:number|null;direction:'increase'|'decrease'|'unchanged'|null}[];
}
export interface EvaluationExecution {
 run_id:string;summary:string;unavailable?:boolean;findings?:ComputedFindings;
 method_version?:string;captured_at?:string;dataset_version?:number;case_ref?:{id:string;version:number}|null;
 measurement_items?:{labels:Record<string,string>};
}
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const bounded=(v:unknown,max:number):v is string=>typeof v==='string'&&!!v.trim()&&v.length<=max;
const reported=(v:unknown):v is ReportedValue|null=>v===null||object(v)&&bounded(v.value,300)&&bounded(v.answer_id,36)&&bounded(v.quote,1000)&&v.quote.includes(v.value);
export function validEvaluationClaims(v:unknown):v is EvaluationClaim[]{
 return Array.isArray(v)&&v.length<=12&&v.every(c=>object(c)&&bounded(c.source_term,200)&&bounded(c.target_term,200)&&
  ['reported_influence','co_movement','hypothesis'].includes(String(c.relationship))&&bounded(c.answer_id,36)&&bounded(c.quote,1000)&&
  c.quote.includes(c.source_term)&&c.quote.includes(c.target_term)&&reported(c.direction)&&reported(c.magnitude));
}
export const relationshipLabels={reported_influence:'영향이 있다는 진술',co_movement:'함께 변했다는 관측',hypothesis:'영향 가능성·가설'};
export const reportedDisplay=(value:ReportedValue|null)=>value?.value??'미정 · 언급 없음';
