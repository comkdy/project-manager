import type {RecoveryStatus} from './recovery.generated';

export const recoveryLabels={running:'진행 중',verified:'복구 검증 완료',attention:'확인 필요',failed:'실패',interrupted:'중단됨'};
export function recoveryMatches(data:RecoveryStatus|undefined,workspace:string){return !!data&&data.workspace_token===workspace;}

// Keep the same id after an unknown network outcome. The server returns the
// original job, including when it already finished or was verified again.
export class RecoveryRequests {
 private requests=new Map<string,string>();
 private pending:string|null=null;
 begin(operation:string):string|null {
  if(this.pending!==null)return null;
  const id=this.requests.get(operation)??crypto.randomUUID().replaceAll('-','');
  this.requests.set(operation,id);this.pending=operation;return id;
 }
 finish(success:boolean){if(success&&this.pending!==null)this.requests.delete(this.pending);this.pending=null;}
}
