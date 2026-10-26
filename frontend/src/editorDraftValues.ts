import type {CardSpec,DatasetSpec,Dataset} from './types';
import type {SqlSnapshot} from './sqlDraft';
export interface DatasetEditDraft {name:string;nameDraft:string;editingName:boolean;spec:DatasetSpec;sql:string;editorMode:'form'|'sql';autoGroup:boolean;sqlOrigin:SqlSnapshot|null}
export interface CardEditDraft {card:CardSpec;expressionDraft:string}
export function ganttDraftSource(initial:Pick<CardSpec,'dataset_id'|'dataset_version'>,datasets:Pick<Dataset,'id'|'version'>[]){return {dataset_id:initial.dataset_id||datasets[0]?.id||'',dataset_version:initial.dataset_version??datasets[0]?.version??1}}
const object=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const keys=(value:Record<string,unknown>,allowed:string[])=>Object.keys(value).every(key=>allowed.includes(key));
const text=(value:unknown)=>typeof value==='string';
const optional=(value:unknown,check:(value:unknown)=>boolean)=>value===undefined||value===null||check(value);
const strings=(value:unknown)=>Array.isArray(value)&&value.every(text);
const finite=(value:unknown)=>typeof value==='number'&&Number.isFinite(value);
const scalar=(value:unknown)=>value===null||text(value)||typeof value==='boolean'||finite(value);
const filters=(value:unknown)=>Array.isArray(value)&&value.every(filter=>object(filter)&&keys(filter,['column','op','value'])&&text(filter.column)&&['eq','ne','gt','gte','lt','lte','in','not_in','contains'].includes(String(filter.op))&&(scalar(filter.value)||Array.isArray(filter.value)&&filter.value.every(scalar)));
const specKeys=['source_id','columns','filters','joins','join_types','where_sql','group_by','having_sql','raw_sql','pivot'];
function validSpec(value:unknown):boolean {
 if(!object(value)||!keys(value,specKeys)||!text(value.source_id)||!Array.isArray(value.columns)||!value.columns.every(column=>object(column)&&keys(column,['name','expression'])&&text(column.name)&&text(column.expression))||!filters(value.filters))return false;
 if(!optional(value.joins,strings)||!optional(value.group_by,strings)||!['where_sql','having_sql','raw_sql'].every(key=>optional(value[key],text))||!optional(value.join_types,v=>object(v)&&Object.values(v).every(kind=>kind==='left'||kind==='inner')))return false;
 return optional(value.pivot,v=>object(v)&&keys(v,['keys','operation_column','value_column','time_column','tie_breaker','outputs','require_all','filters','where_sql'])&&strings(v.keys)&&['operation_column','value_column','time_column','tie_breaker'].every(key=>text(v[key]))&&typeof v.require_all==='boolean'&&filters(v.filters)&&optional(v.where_sql,text)&&Array.isArray(v.outputs)&&v.outputs.every(output=>object(output)&&keys(output,['value','name'])&&text(output.value)&&text(output.name)));
}
function validOrigin(value:unknown):boolean {if(value===null)return true;if(!object(value)||!keys(value,['form','sql'])||!text(value.form)||!text(value.sql))return false;try{return validSpec(JSON.parse(value.form as string))}catch{return false}}
export function validDatasetDraft(value:unknown):value is DatasetEditDraft {
 if(!object(value)||!keys(value,['name','nameDraft','editingName','spec','sql','editorMode','autoGroup','sqlOrigin']))return false;
 const spec=value.spec,origin=value.sqlOrigin;
 return typeof value.name==='string'&&typeof value.nameDraft==='string'&&typeof value.editingName==='boolean'&&typeof value.sql==='string'&&['form','sql'].includes(String(value.editorMode))&&typeof value.autoGroup==='boolean'&&validSpec(spec)&&validOrigin(origin);
}
function validOptions(value:unknown):boolean {
 const numeric=['bins','trellis_columns','x_min','x_max','y_min','y_max','x_font_size','y_font_size'],texts=['palette','x_label','y_label'];
 return object(value)&&keys(value,[...numeric,...texts,'sort','x_scale','y_scale','value_format'])&&numeric.every(key=>optional(value[key],finite))&&texts.every(key=>optional(value[key],text))&&optional(value.sort,v=>v==='asc'||v==='desc')&&['x_scale','y_scale'].every(key=>optional(value[key],v=>v==='linear'||v==='log'))&&optional(value.value_format,v=>v==='number'||v==='percent');
}
function validPeriod(value:unknown):boolean {return object(value)&&keys(value,['use_dashboard','source_id','column'])&&typeof value.use_dashboard==='boolean'&&optional(value.source_id,text)&&optional(value.column,text)}
function validGantt(value:unknown):boolean {return object(value)&&keys(value,['data_mode','status_source_id','tat_source_id','history_source_id','line_filters','key_filters','started_column','as_of'])&&['status_source_id','tat_source_id','history_source_id','started_column'].every(key=>optional(value[key],text))&&optional(value.as_of,v=>typeof v==='string'&&(!v||Number.isFinite(Date.parse(v))))&&optional(value.line_filters,filters)&&optional(value.key_filters,filters)&&optional(value.data_mode,v=>v==='tables'||v==='dataset')}
export function validCardDraft(value:unknown):value is CardEditDraft {
 if(!object(value)||!keys(value,['card','expressionDraft'])||typeof value.expressionDraft!=='string'||!object(value.card))return false;
 const card=value.card;
 return keys(card,['editor_mode','python_script','script_input','gantt','period','id','title','dataset_id','dataset_version','kind','renderer','x','y','color','trellis','aggregate','value_expression','value_expression_name','filters','options'])&&typeof card.id==='string'&&typeof card.title==='string'&&typeof card.dataset_id==='string'&&finite(card.dataset_version)&&['kpi','scatter','bar','line','histogram','box','wafer','gantt'].includes(String(card.kind))&&['plotly','seaborn'].includes(String(card.renderer))&&['count','sum','avg','min','max','yield','none'].includes(String(card.aggregate))&&filters(card.filters)&&validOptions(card.options)&&['x','y','color','trellis','value_expression_name','python_script'].every(key=>optional(card[key],text))&&optional(card.editor_mode,v=>v==='simple'||v==='python')&&optional(card.script_input,v=>v==='dataset'||v==='chart')&&optional(card.period,validPeriod)&&optional(card.gantt,validGantt)&&optional(card.value_expression,v=>Array.isArray(v)&&v.every(token=>object(token)&&((token.kind==='column'&&keys(token,['kind','name'])&&text(token.name))||(token.kind==='number'&&keys(token,['kind','value'])&&text(token.value))||(token.kind==='operator'&&keys(token,['kind','value'])&&['+','-','*','/','(',')'].includes(String(token.value))))));
}
export function datasetDraftComparison(value:Pick<DatasetEditDraft,'name'|'spec'|'sql'|'editorMode'>){return {'이름':value.name,'작성 방식':value.editorMode==='sql'?'SQL':'간편 작성기',...(value.editorMode==='sql'?{'조회 SQL':value.sql}:{'조회 정의':value.spec})}}
export function cardDraftComparison(card:CardSpec|null){if(!card)return '이 카드는 최신 보드에서 삭제되었습니다.';return {'카드 제목':card.title,'차트 종류':card.kind,'데이터셋':card.dataset_id,'데이터셋 버전':card.dataset_version,'Python 스크립트':card.python_script??undefined,'X축':card.x,'Y축':card.y,'셀 값·그룹':card.color,'집계':card.aggregate,'계산식':card.value_expression,'필터':card.filters,'기간':card.period,'표시 설정':card.options,'간트 설정':card.gantt}}
