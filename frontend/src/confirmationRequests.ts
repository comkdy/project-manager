import {questionProjectId} from './semanticCollectionTypes.ts';
import type {KnowledgeQuestion} from './semanticCollectionTypes.ts';

export type ConfirmationStep='answer'|'approval'|'processing'|'completed'|'deferred'|'attention';
export const confirmationLabels:Record<ConfirmationStep,string>={answer:'추가 설명 필요',approval:'승인 필요',processing:'AI 정리 중',completed:'반영 완료',deferred:'보류',attention:'처리 확인 필요'};

export function confirmationStep(q:KnowledgeQuestion):ConfirmationStep{
 if(q.status==='answered')return 'completed';
 if(q.dataset_changed)return 'attention';
 if(q.status==='structuring')return 'processing';
 if(q.status==='deferred')return 'deferred';
 if(q.status==='review')return q.followups.length?'answer':'approval';
 if(q.answers?.at(-1)?.status==='error')return 'attention';
 return 'answer';
}

export function confirmationMatches(q:KnowledgeQuestion,filter:string,selected=false,now=Date.now()){
 if(selected||filter==='all')return true;
 const step=confirmationStep(q);
 if(filter==='pending')return step!=='completed'&&(step!=='deferred'||!!q.deferred_until&&new Date(q.deferred_until).getTime()<=now);
 return step===filter;
}

export function confirmationInProject(q:KnowledgeQuestion,project:string){
 return project==='all'||(project==='shared'?questionProjectId(q)===null:String(questionProjectId(q))===project);
}
