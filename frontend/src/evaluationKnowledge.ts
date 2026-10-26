import type {CollectionDataset} from './semanticCollectionTypes.ts';

export type EvaluationInput = {projectId:number|null;text:string}|{datasetId:string;text:string};
export interface EvaluationRegistration {question_id:string;answer_id:string;status:string}
export const blankEvaluationInput=():EvaluationInput=>({projectId:null,text:''});
export function validEvaluationInput(value:unknown):value is EvaluationInput{
 if(!value||typeof value!=='object'||Array.isArray(value))return false;
 const v=value as Record<string,unknown>;
 return Object.keys(v).length===2&&((typeof v.datasetId==='string'&&v.datasetId.length<=80)||
  ('projectId' in v&&(v.projectId===null||typeof v.projectId==='number'&&Number.isInteger(v.projectId)&&v.projectId>0)))&&
  typeof v.text==='string'&&v.text.length<=6000;
}
export function evaluationRegistrationBody(value:EvaluationInput,options:({id:number;name:string}|CollectionDataset)[]){
 if(!validEvaluationInput(value)||!value.text.trim())throw new Error('평가한 내용과 관측 결과를 입력하세요.');
 if('datasetId' in value){
  if(!options.some(d=>d.id===value.datasetId))throw new Error('현재 사용할 수 있는 관련 데이터셋을 선택하세요.');
  return {dataset_id:value.datasetId,text:value.text.trim()};
 }
 if(value.projectId!==null&&!options.some(p=>p.id===value.projectId))throw new Error('현재 사용할 수 있는 적용 범위를 선택하세요.');
 return {project_id:value.projectId,text:value.text.trim()};
}
export const evaluationScopeLabel=(dataset:{project_id:number|null},projects:{id:number;name:string}[])=>
 dataset.project_id===null?'공통 데이터셋':projects.find(p=>p.id===dataset.project_id)?.name??`프로젝트 #${dataset.project_id}`;
export const evaluationDatasetNotice=(context?:{dataset_changed?:boolean}|null)=>context?.dataset_changed===true?
 '연결한 데이터셋이 변경되어 현재 데이터의 관련 사례 검색에서 제외됩니다. 현재 데이터셋으로 사례를 다시 등록하고 검토해 주세요.':null;
