import type {ActivityControls} from './workspaceActivity.generated';
import type {DefinitionRef,KnowledgeContent} from './semanticTypes';

export type AnalysisFamily = 'inline' | 'epm';
export type AnalysisKind = 'statistics' | 'trend' | 'split';
export type RetestPolicy = 'latest_per_site' | 'all_events';
export type AnalysisStatus = 'queued' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed' | 'needs_input';
export interface AnalysisSeries {family:AnalysisFamily;operation:string|null;parameter:string}
export interface AnalysisSeriesCriterion {family:AnalysisFamily;operation:string;parameter:string;min_samples:number;absolute_delta:number}
export interface AnalysisCatalog {
 sources:{family:AnalysisFamily;source_id:number|string;name:string;available:boolean}[];
 series:AnalysisSeries[];
 recipe_operations:{operation:string;recipes:string[]}[];
 extent:{start:string;end:string}|null;
 limits:{max_rows:number};
 supported:boolean;
 issues:string[];
}
export interface AnalysisRequest {
 project_id?:number|null;_project_scope?:{id:number;name:string;lot_cds:string[]};
 kind:AnalysisKind;purpose?:'analysis'|'validation';family?:AnalysisFamily;operation?:string;parameter?:string;
 baseline_start?:string;baseline_end?:string;current_start?:string;current_end?:string;
 start?:string;end?:string;recipe_a?:string;recipe_b?:string;
 lot_ids_a?:string[];lot_ids_b?:string[];retest_policy?:RetestPolicy;
 min_samples?:number;absolute_delta?:number;series_criteria?:AnalysisSeriesCriterion[];bucket?:'day';knowledge_refs?:DefinitionRef[];
}
export interface AnalysisStatistics {
 event_count:number;valid_count:number;missing_count:number;wafer_count:number;lot_count:number;
 min:number|null;max:number|null;mean:number|null;median:number|null;stddev:number|null;
 q25:number|null;q75:number|null;first_at:string|null;last_at:string|null;
}
export interface AnalysisPoint {at:string;event_count:number;valid_count:number;missing_count:number;mean:number|null}
export interface AnalysisResultSeries extends AnalysisSeries {
 statistics?:AnalysisStatistics;baseline?:AnalysisStatistics;current?:AnalysisStatistics;
 group_a?:AnalysisStatistics;group_b?:AnalysisStatistics;
 delta?:number|null;percent_change?:number|null;
 status?:'needs_criteria'|'insufficient_data'|'changed'|'stable';
 points?:AnalysisPoint[];
 criteria?:{min_samples:number|null;absolute_delta:number|null;source?:string};
}
export interface AnalysisResult {
 series:AnalysisResultSeries[];row_count?:number;warnings:string[];
 windows?:{baseline:{start:string;end:string};current:{start:string;end:string}};
 window?:{start:string;end:string};bucket?:string;retest_policy?:RetestPolicy;
 criteria?:{min_samples:number|null;absolute_delta:number|null};
 operation?:string;recipes?:{a:string;b:string};
 matched_count?:number;unmatched_count?:number;
 excluded_ambiguous_wafer_count?:number;excluded_ambiguous_measurement_count?:number;
 overlapping_lots?:{lot_cd:string;lot_id:string}[];
}
export interface AnalysisRun {
 schedule_id?:string|null; scheduled_for?:string|null;
 id:string;kind:AnalysisKind;status:AnalysisStatus;request:AnalysisRequest;
 revision?:number;retry_of?:string|null;retry_run_id?:string|null;controls?:ActivityControls;
 result:AnalysisResult|null;questions:AnalysisQuestion[];
 error:string|null;created_at:string;completed_at:string|null;
 source_watermark:{captured_at:string;sources:Record<string,{source_id:string;row_count:number;last_measured_at:string|null;max_record_id:number|null}>;isolation:string;scope:string}|null;
}
export interface ApprovedAnalysisDefinition extends DefinitionRef {content:KnowledgeContent}
export interface AnalysisQuestion {id:string;title:string;status:string;approved_definition?:ApprovedAnalysisDefinition}
