import type {ActivityActionRequest,ActivityControls,ActivityTarget} from './workspaceActivity.generated.ts';

export function jobActionBody(target:ActivityTarget,controls:ActivityControls,action:ActivityActionRequest['action'],workspace:string):ActivityActionRequest{
 return {target:{...target},action,expected_revision:controls.revision,workspace_token:workspace};
}
export function jobActionCurrent(body:ActivityActionRequest,target:ActivityTarget,controls:ActivityControls|undefined|null,workspace:string){
 return body.workspace_token===workspace&&body.target.kind===target.kind&&body.target.id===target.id&&
  (body.target.project_id??null)===(target.project_id??null)&&body.expected_revision===controls?.revision&&
  (body.action==='cancel'?controls.can_cancel===true:controls.can_retry===true);
}
export function jobActionExplanation(target:ActivityTarget,action:ActivityActionRequest['action']){
 if(action==='retry')return target.kind==='knowledge_collection'?'현재 수집 설정으로 다시 실행합니다. 기존 질문·답변·승인은 유지하며, 이미 수집한 질문은 중복 생성하지 않습니다.':'원래 분석 기록을 보존하고 같은 조건의 새 작업을 만듭니다. 원천 데이터는 다시 읽으므로 결과가 달라질 수 있습니다. 기존 질문·답변·승인은 유지합니다.';
 return target.kind==='analysis_proposal'?'이 조건안을 취소합니다. 새 분석이나 질문은 만들지 않으며, 기존 질문·답변·승인은 유지합니다.':'대기 중이면 바로 취소합니다. 실행 중이면 중단을 확인할 때까지 취소 요청됨으로 표시합니다. 이미 진행 중인 조회나 AI 응답을 기다릴 수 있으며, 취소 요청 뒤 결과는 반영하지 않습니다. 기존 질문·답변·승인은 유지합니다.';
}
