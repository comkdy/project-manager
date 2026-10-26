import type {ChatKnowledge} from './chatKnowledge.generated';
export type Role = 'admin'|'editor'|'viewer';
export interface User {id:string;username:string;employee_id:string|null;name:string;team:string;role:Role;active:boolean;profile_source:'local'|'sso'|'legacy';version:number}
export interface AuthSession {mode:'local_admin'|'sso';user:User|null;status:'authenticated'|'unauthenticated'|'unavailable'|'disabled';message?:string}
export interface Filter {column:string;op:'eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'in'|'not_in'|'contains';value:unknown}
export interface ISOWeek {year:number;week:number}
export interface CardPeriod {use_dashboard:boolean;source_id?:string|null;column?:string|null}
export interface QueryContext {gantt_detail_lot?:string;gantt_page?:number;gantt_search?:string;gantt_lot_cd?:string|null;period_start?:ISOWeek;project_id?:number|null;filters?:Filter[];limit?:number;cursor?:string|null;sort?:string;direction?:'asc'|'desc';refresh?:boolean}
export interface Column {name:string;type:string}
export interface Source {id:string;name:string;schema_name:string;table_name:string;project_column:string|null;primary_key:string;columns:Column[];default_sql?:string}
export interface PivotSpec {keys:string[];operation_column:string;value_column:string;time_column:string;tie_breaker:string;outputs:{value:string;name:string}[];require_all:boolean;filters:Filter[];where_sql?:string|null}
export interface DatasetSpec {source_id:string;columns:{name:string;expression:string}[];filters:Filter[];joins?:string[];join_types?:Record<string,'left'|'inner'>;where_sql?:string|null;group_by?:string[];having_sql?:string|null;raw_sql?:string|null;pivot?:PivotSpec|null}
export interface Dataset {id:string;name:string;version:number;project_id:number|null;kind?:'source'|'derived';columns:Column[];spec:DatasetSpec;source_tables?:string[]|null}
export interface SimpleEditorState {available:boolean;reason:string|null;spec?:DatasetSpec}
export interface DatasetSql {sql:string;spec:DatasetSpec;source_tables:string[];project_id:number|null;scope_note:string;simple_editor:SimpleEditorState}
export type ChartKind = 'kpi'|'scatter'|'bar'|'line'|'histogram'|'box'|'wafer'|'gantt';
export type ValueToken = {kind:'column';name:string}|{kind:'number';value:string}|{kind:'operator';value:'+'|'-'|'*'|'/'|'('|')'};
export interface GanttSettings {data_mode?:'tables'|'dataset';status_source_id?:string|null;tat_source_id?:string|null;history_source_id?:string|null;line_filters?:Filter[];key_filters?:Filter[];started_column?:string|null;as_of?:string|null}
export interface CardSpec {editor_mode?:'simple'|'python';python_script?:string|null;script_input?:'dataset'|'chart';gantt?:GanttSettings;period?:CardPeriod;id:string;title:string;dataset_id:string;dataset_version:number;kind:ChartKind;renderer:'plotly'|'seaborn';x?:string;y?:string;color?:string;trellis?:string|null;aggregate:'count'|'sum'|'avg'|'min'|'max'|'yield'|'none';value_expression?:ValueToken[]|null;value_expression_name?:string|null;filters:Filter[];options:{palette?:string;bins?:number;trellis_columns?:number;sort?:'asc'|'desc';x_min?:number|null;x_max?:number|null;y_min?:number|null;y_max?:number|null;x_font_size?:number;y_font_size?:number;x_scale?:'linear'|'log';y_scale?:'linear'|'log';x_label?:string|null;y_label?:string|null;value_format?:'number'|'percent'}}
export interface GridItem {i:string;x:number;y:number;w:number;h:number}
export interface Dashboard {id:string;project_id:number|null;version:number;cards:CardSpec[];layout:GridItem[]}
export interface Project {id:number;name:string;code:string}
export interface Bootstrap {user:User;projects:Project[];datasets:Dataset[];catalog:Source[];llm_configured:boolean;workspace_token:string}
export interface LlmStatus {server_configured:boolean;configured:boolean;key_registered:boolean;base_url:string;model:string;status:'not_registered'|'unchecked'|'connected'|'error';checked_at:string|null;message:string}
export interface DatabaseStatus {status:'connected'|'error'|'not_configured';workspace_token:string;profile_id:string|null;database?:string;message?:string}
export interface QueryResult {columns:Column[];rows:Record<string,unknown>[];next_cursor:string|null;meta:Record<string,unknown>}
export interface PreviewResult extends QueryResult {before_rows:Record<string,unknown>[];sql:string;canonical_sql:string;spec:DatasetSpec;simple_editor:SimpleEditorState}
export interface ChartResult {figure?:{data:Record<string,unknown>[];layout:Record<string,unknown>};rows:{x?:unknown;y?:unknown;color?:unknown;trellis?:unknown;value?:number}[];meta:{mode:'full'|'sample'|'aggregate';returned_rows:number;queried_at:string;cached:boolean;ignored_filters:string[];[key:string]:unknown}}
export interface Proposal {evaluation_dataset_recommendation_id?:string;knowledge?:ChatKnowledge;knowledge_context_id?:string;period_start?:ISOWeek|null;kind:'query'|'card'|'dataset';explanation:string;card?:CardSpec;dataset?:{name:string;spec:DatasetSpec};proposal_id?:string;workspace_token?:string;project_id?:number|null;query_result?:QueryResult;card_result?:ChartResult;steps?:string[]}
export interface Relation {id:string;left_source_id:string;right_source_id:string;left_column:string;right_column:string;join_type:'left'|'inner'}
