import type {Filter,ISOWeek} from './types.ts';
import type {ActivityControls} from './workspaceActivity.generated.ts';

export interface EvaluationAnalysisSettings {
 method:'group_compare'|'correlation'|'ml_regression';group_column?:string;metrics:string[];baseline?:string;comparison?:string;
 features?:string[];unit_columns?:string[];time_column?:string;
}
export interface EvaluationMetric {column:string;valid_count:number;missing_count:number;mean:number|null;stddev:number|null;min:number|null;max:number|null}
export interface EvaluationAnalysisResult {
 item_labels?:Record<string,string>;
 method:'group_compare'|'correlation'|'ml_regression';method_version:string;summary:string;captured_at:string;row_count:number;data_fingerprint?:string;
 ml?:{target:string;status:string;interpretation:string;valid_rows:number;excluded_rows:number;
  validation:{train_rows:number;test_rows:number;purged_rows:number;cutoff:string;unit_columns:string[]}|null;
  metrics:{mae:number;baseline_mae:number;r2:number;beats_baseline:boolean}|null;
  importance:{column:string;mae_increase:number;std:number}[];
  statistics:{column:string;count:number;mean:number|null;stddev:number|null;pearson_to_target:number|null}[];
  groups:{column:string;values:{value:string;count:number;target_mean:number}[]}[]};
 groups?:{value:string|null;row_count:number;metrics:EvaluationMetric[]}[];
 deltas?:{column:string;baseline:string;comparison:string;delta:number|null;percent_change:number|null}[];
 correlation?:{x:string;y:string;pair_count:number;excluded_count:number;pearson:number|null}|null;
 warnings:string[];knowledge_eligible?:boolean;
}
export interface EvaluationAnalysisReview {question_id:string;draft_id:string;status:string;item_id?:string|null;item_version?:number|null}
export interface EvaluationRecommendation {
 status:'running'|'waiting'|'ready'|'error';summary?:string;message?:string;settings?:EvaluationAnalysisSettings|null;
 columns?:{column:string;role:'target'|'feature'|'group'|'unit'|'time';reason:string;evidence:{reference:string;quote:string}}[];
 row_basis?:{reference:string;quote:string}|null;limitations?:string[];
 questions:{id:string;title:string;status:string}[];
}
export interface EvaluationAnalysisView {
 id:string;kind:'evaluation';revision:number;status:string;workspace_token:string;project_id:number|null;
 dataset:{id:string;name:string;version:number;project_id:number|null};case_ref:{id:string;version:number;name:string}|null;
 origin?:'chat'|'case';question?:string|null;
 knowledge_basis?:Record<string,string>;
 recommendation?:EvaluationRecommendation|null;
 columns:{name:string;type:string;numeric:boolean}[];
 scope:{filters:Filter[];period_start?:ISOWeek|null;period?:{start:ISOWeek;source_id:string;column:string;label:string;timezone:string}|null;card_title?:string|null};
 settings:EvaluationAnalysisSettings|null;result:EvaluationAnalysisResult|null;review?:EvaluationAnalysisReview|null;
 controls?:ActivityControls|null;
 error?:string|null;warnings:string[];created_at:string;
}
export interface EvaluationAnalysisInput {method:EvaluationAnalysisSettings['method'];groupColumn:string;metrics:string[];baseline:string;comparison:string;features:string[];unitColumns:string[];timeColumn:string}
export const blankEvaluationAnalysis=():EvaluationAnalysisInput=>({method:'group_compare',groupColumn:'',metrics:[],baseline:'',comparison:'',features:[],unitColumns:[],timeColumn:''});
export function evaluationAnalysisInput(settings:EvaluationAnalysisSettings|null):EvaluationAnalysisInput {
 return settings?{method:settings.method,groupColumn:settings.group_column??'',metrics:[...settings.metrics],baseline:settings.baseline??'',comparison:settings.comparison??'',features:[...(settings.features??[])],unitColumns:[...(settings.unit_columns??[])],timeColumn:settings.time_column??''}:blankEvaluationAnalysis();
}
export function evaluationAnalysisSettings(input:EvaluationAnalysisInput,columns:EvaluationAnalysisView['columns']):EvaluationAnalysisSettings {
 const names=new Set(columns.map(column=>column.name)),numeric=new Set(columns.filter(column=>column.numeric).map(column=>column.name));
 if(!['group_compare','correlation','ml_regression'].includes(input.method))throw new Error('분석 방법을 선택해 주세요.');
 if(input.method==='ml_regression'){
  const all=[...input.metrics,...input.features,...input.unitColumns,input.timeColumn];
  if(input.metrics.length!==1||!numeric.has(input.metrics[0])||!input.features.length||input.features.length>8||!input.unitColumns.length||input.unitColumns.length>4||all.some(c=>!names.has(c))||new Set(all).size!==all.length)throw new Error('목표 수치 1개, 설명 항목 1~8개, Lot·wafer 구분, 측정 시점을 서로 다른 컬럼으로 선택하세요.');
  if(!/^(timestamp|date)/i.test(columns.find(c=>c.name===input.timeColumn)?.type??''))throw new Error('검증 기간을 나눌 날짜·시간 항목을 선택하세요.');
  return {method:input.method,metrics:[...input.metrics],features:[...input.features],unit_columns:[...input.unitColumns],time_column:input.timeColumn};
 }
 if(input.metrics.length<1||input.metrics.length>2||new Set(input.metrics).size!==input.metrics.length||input.metrics.some(metric=>!numeric.has(metric)))throw new Error('현재 데이터셋의 숫자 항목을 1~2개 선택해 주세요.');
 if(input.method==='correlation'){
  if(input.metrics.length!==2)throw new Error('함께 변하는 관계를 확인할 숫자 항목 2개를 선택해 주세요.');
  return {method:input.method,metrics:[...input.metrics]};
 }
 if(!names.has(input.groupColumn))throw new Error('Recipe·장비 등 비교 그룹을 나눌 항목을 선택해 주세요.');
 if(input.metrics.includes(input.groupColumn))throw new Error('비교 그룹 항목과 측정 항목은 다르게 선택해 주세요.');
 const hasBaseline=!!input.baseline.trim(),hasComparison=!!input.comparison.trim();
 if(hasBaseline!==hasComparison)throw new Error('평균 차이를 계산하려면 기준 그룹과 비교 그룹을 모두 입력해 주세요.');
 if(hasBaseline&&input.baseline===input.comparison)throw new Error('서로 다른 두 그룹을 입력해 주세요.');
 return {method:input.method,group_column:input.groupColumn,metrics:[...input.metrics],...(hasBaseline?{baseline:input.baseline,comparison:input.comparison}:{})};
}
export function evaluationAnalysisCurrent(run:EvaluationAnalysisView|undefined,id:string,workspace:string,project:number|null){
 return !!run&&run.id===id&&run.workspace_token===workspace&&run.project_id===project;
}
export const evaluationAnalysisBusy=(run:Pick<EvaluationAnalysisView,'status'>|undefined)=>!!run&&['queued','running','cancelling'].includes(run.status);
export function evaluationRecommendationBody(run:EvaluationAnalysisView,workspace:string,continuous:boolean,interval:number){
 if(run.workspace_token!==workspace||run.status!=='needs_input'||run.recommendation?.status!=='ready'||!run.recommendation.settings||run.recommendation.questions.some(q=>q.status!=='answered'))throw new Error('승인된 연결과 최신 AI 추천 조건을 먼저 확인하세요.');
 if(!Number.isInteger(interval)||interval<15||interval>10080)throw new Error('데이터 확인 간격을 확인하세요.');
 evaluationAnalysisSettings(evaluationAnalysisInput(run.recommendation.settings),run.columns);
 return {expected_revision:run.revision,workspace_token:workspace,settings:run.recommendation.settings,continuous,interval_minutes:interval};
}
export function evaluationAnalysisExecuteBody(run:EvaluationAnalysisView,input:EvaluationAnalysisInput,workspace:string){
 if(run.workspace_token!==workspace||run.status!=='needs_input')throw new Error('최신 분석 상태를 확인한 뒤 다시 진행해 주세요.');
 return {expected_revision:run.revision,workspace_token:workspace,settings:evaluationAnalysisSettings(input,run.columns)};
}
export function evaluationAnalysisReviewBody(run:EvaluationAnalysisView,workspace:string,note:string){
 if(run.workspace_token!==workspace||run.status!=='completed'||!run.result)throw new Error('완료된 분석 결과를 먼저 확인해 주세요.');
 if(run.result.knowledge_eligible===false)throw new Error('유효한 분석 수치가 부족해 지식 검토를 요청할 수 없습니다. 다른 데이터 범위나 측정 항목을 선택해 주세요.');
 return {expected_revision:run.revision,workspace_token:workspace,...(note.trim()?{note:note.trim()}:{})};
}
export const evaluationAnalysisNumber=(value:number|null|undefined)=>value==null||!Number.isFinite(value)?'—':value.toLocaleString('ko-KR',{maximumSignificantDigits:7});
export function evaluationScopeFilters(filters:Filter[]){
 const operators:Record<string,string>={eq:'=',ne:'≠',gt:'>',gte:'≥',lt:'<',lte:'≤',in:'포함',not_in:'제외',contains:'문자 포함'};
 return filters.map(filter=>`${filter.column} ${operators[filter.op]??filter.op} ${Array.isArray(filter.value)?filter.value.map(String).join(', '):String(filter.value??'빈 값')}`);
}
