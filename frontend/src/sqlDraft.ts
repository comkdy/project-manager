import type {DatasetSpec} from './types';

export interface SqlSnapshot {form:string;sql:string}

export function sqlSnapshot(spec:DatasetSpec,sql:string):SqlSnapshot{
 return {form:JSON.stringify(spec),sql};
}

export function editorRequest(mode:'form'|'sql',spec:DatasetSpec,sql:string,snapshot:SqlSnapshot|null){
 if(mode==='form'&&spec.pivot)return {editor_mode:'simple' as const,spec:{...spec,raw_sql:null}};
 if(mode==='sql'&&snapshot?.sql===sql){
  const original=JSON.parse(snapshot.form) as DatasetSpec;
  if(original.pivot)return {editor_mode:'sql' as const,spec:{...original,raw_sql:sql}};
 }
 // Parsing SQL into controls does not authorize regenerating the SQL. Reuse
 // the original text until the user actually changes those controls.
 const original=mode==='sql'?sql:snapshot?.form===JSON.stringify(spec)?snapshot.sql:null;
 if(original!==null)return {editor_mode:'sql' as const,spec:{source_id:spec.source_id,columns:[],filters:[],raw_sql:original}};
 return {editor_mode:'simple' as const,spec};
}
