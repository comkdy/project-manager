import type {KnowledgeContent,KnowledgeKind} from './semanticTypes';
import type {EvaluationExecution} from './evaluationRelationships';

export interface CollectionColumn {column:string;source_id:string;source_name:string}
export interface QuestionTarget {kind:'dataset'|'column'|'column_value';column:string;source_id:string;value:string;source_name?:string}
export interface CollectionDataset {id:string;version:number;name:string;project_id:number|null;target_columns?:CollectionColumn[]}
export interface CollectionSettings {
 revision:number;enabled:boolean;interval_hours:number;max_questions:number;scope:'all_datasets';
 configured:boolean;datasets:CollectionDataset[];next_run:string|null;last_run:string|null;last_status:string;last_message:string;
}
export interface KnowledgeAnswer {id:string;text:string;targets:QuestionTarget[];user_id:string;user_name:string;status:string;error:string;created_at:string}
export interface KnowledgeQuestion {
 id:string;title:string;reason:string;kind:KnowledgeKind;topic:string;subject:string;status:string;revision:number;
 dataset:CollectionDataset|null;case_context?:{project_id:number|null}|null;interpretation?:{description:string;details:KnowledgeContent['details'];terms:EvaluationTermView[];issues:string[]};dataset_changed:boolean;deferred_until:string|null;created_at:string;followups:string[];answers?:KnowledgeAnswer[];
 targets:QuestionTarget[];review_feedback?:string;
 dataset_setup?:{recommendation_id:string;project_id:number|null}|null;
 analysis_setup?:{run_id:string;project_id:number|null}|null;
 execution_evidence?:EvaluationExecution|null;
 draft?:{id:string;question_id:string;answer_id:string;status:string;revision:number;item_id:string|null;item_version:number|null}|null;
}
export interface CollectedProposal {kind:KnowledgeKind;scope:'common'|'database';content:KnowledgeContent}
export interface CollectedDraft {
 id:string;question_id:string;answer_id:string;proposal:CollectedProposal;original_proposal:CollectedProposal;
 evidence:Record<string,{value:string;answer_id:string;quote:string}>;followups:string[];status:string;revision:number;review_note:string;
 question:KnowledgeQuestion;issues:string[];conflicts:{id:string;name:string;revision:number;kind:KnowledgeKind;scope:string}[];
 item_id?:string|null;item_version?:number|null;
}
export const questionStatuses:Record<string,string>={open:'추가 설명 필요',deferred:'보류',structuring:'AI 정리 중',review:'승인 필요',answered:'반영 완료'};
export const topics:Record<string,string>={grain:'한 행의 의미',column_meaning:'컬럼 의미',calculation:'계산 기준',time_basis:'시간 기준',exclusions:'제외 조건',deduplication:'중복·재검사',relationship:'데이터 관계',other:'기타 업무 의미'};

export interface EvaluationTermView {term:string;status:string;explanation:string;quote:string;answer_id:string;available?:boolean;references:{item_id:string;version:number;name:string;binding_version:number|null;binding_fingerprint:string}[];bindings?:unknown[]}
export const questionProjectId=(q:Pick<KnowledgeQuestion,'dataset'|'case_context'|'analysis_setup'>):number|null=>q.analysis_setup?q.analysis_setup.project_id:q.case_context?.project_id??q.dataset?.project_id??null;
