import type {OperationCheck,OperationsStatus} from './operations.generated';
export type OperationAction=NonNullable<OperationCheck['action']>;
export type OperationJump={target:'activity'|'schedules'|'recovery';token:number};
export const operationNames={ok:'확인됨',attention:'확인 필요',unknown:'미확인',info:'참고'};
export const operationActions:Record<OperationAction,string>={database:'DB 설정 열기',activity:'전체 작업 목록 보기',knowledge:'확인 요청 열기',schedules:'전체 정기 분석 보기',recovery:'백업·복구 준비 보기',llm_settings:'내 AI 연결 설정'};
export function operationsMatches(data:OperationsStatus|undefined,workspace:string){return !!data&&data.workspace_token===workspace;}
export function operationsExpired(stamp:string,now=Date.now()){const time=Date.parse(stamp);return !Number.isFinite(time)||time>now+5000||now-time>60000;}
export function operationCounts(checks:OperationCheck[]){return checks.reduce((counts,item)=>({...counts,[item.status]:counts[item.status]+1}),{ok:0,attention:0,unknown:0,info:0});}
