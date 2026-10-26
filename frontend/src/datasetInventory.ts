import type {Dataset} from './types';

export function scopedDatasets(datasets:Dataset[], projectId:number|null){
 return datasets.filter(d=>(d.project_id??null)===projectId);
}

export function datasetGroups(datasets:Dataset[],search=''){
 const matched=datasets.filter(d=>d.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
 return [
  {kind:'source',label:'DB 원본',description:'DB 테이블을 직접 조회합니다.',datasets:matched.filter(d=>d.kind==='source')},
  {kind:'derived',label:'추가 데이터셋',description:'AI 또는 사용자가 만든 조회·결합·계산입니다.',datasets:matched.filter(d=>d.kind!=='source')},
 ];
}
