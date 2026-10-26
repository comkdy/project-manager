import type {EvaluationTermView} from './semanticCollectionTypes';
import type {Source} from './types';
import type {EvaluationClaim,EvaluationExecution} from './evaluationRelationships';

export type KnowledgeKind = 'term'|'metric'|'data'|'relation'|'query'|'evaluation';
export type KnowledgeStatus = 'draft'|'approved'|'retired';
export interface DefinitionRef {item_id:string;version:number}
export interface KnowledgeContent {
 name:string;domain:string;description:string;aliases:string[];unresolved:string[];
 references:DefinitionRef[];details:Record<string,string|null|EvaluationTermView[]|EvaluationClaim[]>;change_note:string;
}
export interface SourceRef {source_id:string;role:string;columns:string[]}
export interface Predicate {source_id:string;column:string;op:'eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'in'|'is_null'|'is_not_null';value:string|number|boolean|null|(string|number|boolean|null)[]}
export interface BindingContent {sources:SourceRef[];predicates:Predicate[];expression:string;notes:string}
export interface ReviewInfo {status:KnowledgeStatus;created_by:string;created_at:string;reviewed_by:string|null;reviewed_at:string|null;review_note:string}
export interface Binding extends ReviewInfo {version:number;revision:number;published_version:number|null;content:BindingContent}
export interface KnowledgeItem extends ReviewInfo {
 id:string;kind:KnowledgeKind;scope:'common'|'database';connection_key:string|null;
 revision:number;version:number;head_version:number;published_version:number|null;
 content:KnowledgeContent;binding:Binding|null;
 evaluation_context?:{dataset:{id:string;name:string;version:number;project_id:number|null}|null;case_context?:{project_id:number|null}|null;question_id:string;dataset_changed?:boolean;term_links?:EvaluationTermView[];execution_evidence?:EvaluationExecution|null}|null;
 validation:{definition_issues:string[];binding_issues:string[];schema_revision:string;basis:string;sql_execution:string;business_correctness:string};
}
export interface KnowledgeList {items:KnowledgeItem[];connection_key:string;workspace_token:string}
export interface SemanticCatalog {sources:Source[];connection_key:string;workspace_token:string;schema_revision:string}
export interface ContextItem {id:string;kind:KnowledgeKind;version:number;content:KnowledgeContent;binding:{version:number;content:BindingContent}|null;match_reasons:string[]}
export interface SemanticContext {
 context_id:string;resolution:'resolved'|'needs_clarification'|'missing_knowledge';
 items:ContextItem[];clarifications:{id:string;reason:string;blocking:boolean;options:(DefinitionRef&{name:string;domain:string})[]}[];
 gaps:{reason:string}[];warnings:string[];confirmed_conditions:Record<string,{value:string;source:string}>;
 output_constraints:{item_id:string;version:number;grain:string;unit:string;aggregation:string}[];
}
export const kindNames:Record<KnowledgeKind,string>={term:'용어',metric:'지표',data:'데이터 설명',relation:'테이블 관계',query:'검증된 질의',evaluation:'평가 사례'};
export const statusNames:Record<KnowledgeStatus,string>={draft:'초안',approved:'승인됨',retired:'폐기됨'};
export const blankContent=():KnowledgeContent=>({name:'',domain:'',description:'',aliases:[],unresolved:[],references:[],details:{},change_note:''});
export const blankBinding=():BindingContent=>({sources:[],predicates:[],expression:'',notes:''});

export const detailFields:Record<KnowledgeKind,[string,string][]>={
 term:[],
 metric:[['formula','계산식'],['numerator','분자'],['denominator','분모'],['grain','계산 대상 단위'],
  ['unit','출력 단위'],['time_basis','시간 기준'],['time_window','기간 규칙'],['timezone','시간대'],
  ['exclusions','제외 조건'],['retest_policy','재검사 처리'],['deduplication','중복 처리'],
  ['zero_policy','분모 0 처리'],['aggregation','재집계 규칙']],
 data:[['grain','테이블 한 행의 의미'],['column_notes','컬럼 설명']],
 relation:[['evidence','관계 확인 근거'],['duplication_notes','중복 집계 주의사항'],['join_notes','연결 조건 설명']],
 query:[['question','검토한 질문'],['sql','SQL 원문'],['assumptions','전제 조건'],['expected_result','기대 결과·업무 검토 근거']],
 evaluation:[['operation','평가한 공정'],['recipe_changes','Recipe 변경 내용'],['inline_observations','Inline에서 관측한 변화'],['epm_observations','EPM에서 관측한 변화'],['conditions','평가 조건·대상'],['evidence','평가 근거'],['limitations','적용 범위·확인할 한계'],['analysis_result','현재 데이터 분석 결과']],
};
