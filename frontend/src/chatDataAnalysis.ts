import type {EvaluationAnalysisView} from './evaluationAnalysis';

export interface ChatDataAnalysis {
 kind:'data_analysis';run_id?:string;project_id?:number|null;status:string;explanation:string;
 next_checks?:string[];notice?:string;dataset?:EvaluationAnalysisView['dataset'];
 settings?:EvaluationAnalysisView['settings'];scope?:EvaluationAnalysisView['scope'];
 result?:EvaluationAnalysisView['result'];review?:EvaluationAnalysisView['review'];
 revision?:number;workspace_token?:string;
 questions?:{id:string;title:string;status:string}[];
 plan?:Pick<NonNullable<EvaluationAnalysisView['recommendation']>,'summary'|'columns'|'row_basis'|'limitations'>;
}
export const dataAnalysisPending=(value:Pick<ChatDataAnalysis,'status'>)=>['queued','running','cancelling','needs_input'].includes(value.status);
export const dataAnalysisReviewable=(value:ChatDataAnalysis)=>value.status==='completed'&&!!value.result?.row_count&&value.result.knowledge_eligible!==false;
export function dataAnalysisPath(id:string,project:number|null){return `/chat/data-analysis/${encodeURIComponent(id)}${project===null?'':`?project_id=${project}`}`}
// No card selected means the AI may choose from the current project catalog.
// An unrelated first dataset must never silently become the analysis target.
export function chatSelectedDataset(view:'dashboard'|'data'|'knowledge',card:{dataset_id:string}|null,dataset:string|null){
 return view==='dashboard'?card?.dataset_id??null:view==='data'?dataset:null;
}
