import type {ConversationContext} from './chatHistory';
import type {ChatEvaluationCases,EvaluationSuggestion} from './evaluationCases';
import {evaluationSuggestions} from './evaluationCases.ts';

/** Keep case provenance and the selected data scope together; never execute a prompt. */
export function evaluationPreparation(value:ChatEvaluationCases,suggestion:EvaluationSuggestion,context:ConversationContext,conversationId?:string,sequence?:number){
 if(!evaluationSuggestions(value).some(s=>s.id===suggestion.id&&s.case_ref.id===suggestion.case_ref.id&&s.case_ref.version===suggestion.case_ref.version))throw new Error('이 평가 사례의 현재 버전을 다시 확인해 주세요.');
 if(!context.dataset_id&&!context.card?.dataset_id)throw new Error('분석할 데이터셋이나 카드를 먼저 선택해 주세요.');
 const selectedCard=context.card;
 const card=selectedCard?Object.fromEntries((['dataset_id','dataset_version','title','x','y','color','trellis','filters','period'] as const).filter(key=>selectedCard[key]!==undefined).map(key=>[key,selectedCard[key]])):undefined;
 return {case_ref:suggestion.case_ref,suggestion_id:suggestion.id,search:{query:value.search?.query??suggestion.title,project_id:context.project_id,dataset_id:context.dataset_id??context.card?.dataset_id,card,period_start:context.period_start??undefined},...(conversationId&&sequence?{conversation_id:conversationId,turn_sequence:sequence}:{})};
}

export function messageSequence(id?:string):number|undefined{
 const match=id?.match(/^(\d+)-assistant$/);
 return match?Number(match[1]):undefined;
}
