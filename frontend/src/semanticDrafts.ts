import type {BindingContent,KnowledgeContent,KnowledgeItem,KnowledgeKind,Predicate} from './semanticTypes.ts';
import type {CollectedProposal} from './semanticCollectionTypes.ts';
import {validEvaluationClaims} from './evaluationRelationships.ts';

export type ValueKind='text'|'number'|'boolean'|'text_list'|'number_list';
export type PredicateDraft=Omit<Predicate,'value'>&{value_kind:ValueKind;value_text:string};
export interface DefinitionInput {kind:KnowledgeKind;scope:'common'|'database';content:KnowledgeContent;aliasesText:string;unresolvedText:string}
export interface MappingInput {binding:Pick<BindingContent,'sources'|'expression'|'notes'>;predicates:PredicateDraft[]}
export interface ReviewInput {proposal:CollectedProposal;unresolved:string;note:string;target:string;targetRevision:number|null;dirty:boolean}
export interface AnswerInput {text:string;revision:number|null}
export interface NewQuestionInput {title:string;reason:string;kind:KnowledgeKind;topic:string;subject:string;targetKind:'dataset'|'column'|'column_value';columnKey:string;targetValue:string}

const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const keys=(v:Record<string,unknown>,allowed:string[])=>Object.keys(v).every(k=>allowed.includes(k));
const strings=(v:unknown):v is string[]=>Array.isArray(v)&&v.every(x=>typeof x==='string');
const revision=(v:unknown)=>typeof v==='number'&&Number.isInteger(v)&&v>0;
const kinds=['term','metric','data','relation','query','evaluation'];
export function validKnowledgeContent(v:unknown):v is KnowledgeContent{
 return object(v)&&keys(v,['name','domain','description','aliases','unresolved','references','details','change_note'])&&
  ['name','domain','description','change_note'].every(k=>typeof v[k]==='string')&&strings(v.aliases)&&strings(v.unresolved)&&
  Array.isArray(v.references)&&v.references.every(r=>object(r)&&keys(r,['item_id','version'])&&typeof r.item_id==='string'&&revision(r.version))&&
  object(v.details)&&Object.entries(v.details).every(([key,x])=>x===null||typeof x==='string'||key==='terms'&&validEvaluationTerms(x)||key==='claims'&&validEvaluationClaims(x));
}
export function validDefinitionInput(v:unknown):v is DefinitionInput{
 return object(v)&&keys(v,['kind','scope','content','aliasesText','unresolvedText'])&&kinds.includes(String(v.kind))&&
  ['common','database'].includes(String(v.scope))&&validKnowledgeContent(v.content)&&typeof v.aliasesText==='string'&&typeof v.unresolvedText==='string';
}
export function validMappingInput(v:unknown):v is MappingInput{
 if(!object(v)||!keys(v,['binding','predicates'])||!object(v.binding)||!keys(v.binding,['sources','expression','notes']))return false;
 const b=v.binding;
 return typeof b.expression==='string'&&typeof b.notes==='string'&&Array.isArray(b.sources)&&b.sources.every(s=>object(s)&&keys(s,['source_id','role','columns'])&&typeof s.source_id==='string'&&typeof s.role==='string'&&strings(s.columns))&&
  Array.isArray(v.predicates)&&v.predicates.every(p=>object(p)&&keys(p,['source_id','column','op','value_kind','value_text'])&&typeof p.source_id==='string'&&typeof p.column==='string'&&typeof p.value_text==='string'&&
   ['eq','ne','gt','gte','lt','lte','in','is_null','is_not_null'].includes(String(p.op))&&['text','number','boolean','text_list','number_list'].includes(String(p.value_kind)));
}
export function validReviewInput(v:unknown):v is ReviewInput{
 return object(v)&&keys(v,['proposal','unresolved','note','target','targetRevision','dirty'])&&object(v.proposal)&&keys(v.proposal,['kind','scope','content'])&&
  kinds.includes(String(v.proposal.kind))&&['common','database'].includes(String(v.proposal.scope))&&validKnowledgeContent(v.proposal.content)&&
  ['unresolved','note','target'].every(k=>typeof v[k]==='string')&&(v.targetRevision===null||revision(v.targetRevision))&&typeof v.dirty==='boolean';
}
export const validAnswer=(v:unknown):v is string=>typeof v==='string'&&v.length<=6000;
export const blankQuestionInput=():NewQuestionInput=>({title:'',reason:'',kind:'term',topic:'other',subject:'',targetKind:'dataset',columnKey:'',targetValue:''});
export function validNewQuestionInput(v:unknown):v is NewQuestionInput{
 return object(v)&&keys(v,Object.keys(blankQuestionInput()))&&Object.values(v).every(x=>typeof x==='string')&&
  Object.keys(blankQuestionInput()).every(k=>typeof v[k]==='string')&&kinds.includes(String(v.kind))&&['dataset','column','column_value'].includes(String(v.targetKind));
}
export const definitionVersion=(item:KnowledgeItem|null)=>item?`${item.version}:${item.revision}:${item.status}`:'new';
export const mappingVersion=(item:KnowledgeItem|null)=>item?`${definitionVersion(item)}:${item.binding?.version??0}:${item.binding?.revision??0}:${item.binding?.status??'none'}`:'new';
export const mappingCanRebase=(baseVersion:string,item:KnowledgeItem|null)=>!!item&&baseVersion.split(':')[0]===String(item.version);
export function definitionInput(kind:KnowledgeKind,scope:'common'|'database',content:KnowledgeContent):DefinitionInput{
 return {kind,scope,content,aliasesText:content.aliases.join('\n'),unresolvedText:content.unresolved.join('\n')};
}
export function definitionComparison(value:DefinitionInput){
 return {kind:value.kind,scope:value.scope,content:value.content};
}
export function questionCanAnswer(q:{status:string;dataset_changed:boolean}){
 return !q.dataset_changed&&['open','deferred','review'].includes(q.status);
}

function validEvaluationTerms(value:unknown):boolean{
 return Array.isArray(value)&&value.length<=12&&value.every(t=>object(t)&&keys(t,['term','status','explanation','answer_id','quote','references'])&&
  ['term','status','explanation','answer_id','quote'].every(k=>typeof t[k]==='string')&&Array.isArray(t.references)&&t.references.length<=4&&
  t.references.every(r=>object(r)&&keys(r,['item_id','version','name','binding_version','binding_fingerprint'])&&typeof r.item_id==='string'&&revision(r.version)&&typeof r.name==='string'&&(r.binding_version===null||revision(r.binding_version))&&typeof r.binding_fingerprint==='string'));
}
