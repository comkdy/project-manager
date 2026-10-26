import type {Dataset} from './types';
import './dataset-scope.css';

export default function DatasetScopeBadge({projectId,projectName}:{projectId:number|null|undefined;projectName?:string}){
 const shared=projectId==null;
 return <span className={`dataset-scope-badge ${shared?'shared':'project'}`} title={shared?'전체 프로젝트 화면의 공통 데이터셋입니다.':'이 프로젝트 전용 데이터셋입니다. 서버에서 프로젝트의 데이터 범위를 제한합니다.'}>{shared?'공통':projectName?`프로젝트 · ${projectName}`:'프로젝트 전용'}</span>;
}

export function DatasetKindBadge({kind}:{kind?:Dataset['kind']}){
 return <span className={`dataset-kind-badge ${kind==='source'?'source':'derived'}`} title={kind==='source'?'DB 테이블을 직접 조회하는 원본 데이터셋입니다.':'AI 또는 사용자가 추가한 조회 정의입니다. 원본 DB 테이블을 변경하지 않습니다.'}>{kind==='source'?'DB 원본':'추가 데이터셋'}</span>;
}

export function DatasetLabel({dataset,projectName}:{dataset:Pick<Dataset,'name'|'project_id'|'kind'>;projectName?:string}){
 return <span className="dataset-choice-label"><span>{dataset.name}</span><DatasetKindBadge kind={dataset.kind}/><DatasetScopeBadge projectId={dataset.project_id} projectName={projectName}/></span>;
}
