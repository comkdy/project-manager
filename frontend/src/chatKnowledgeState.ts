import type {ChatKnowledge,KnowledgeClarification,KnowledgeDefinitionRef} from './chatKnowledge.generated.ts';

export function selectKnowledgeDefinition(knowledge:ChatKnowledge,group:KnowledgeClarification,choice:KnowledgeDefinitionRef,previous:KnowledgeDefinitionRef[]=knowledge.selected_definitions??[]):KnowledgeDefinitionRef[]{
 if(knowledge.availability==='unavailable'||knowledge.blocking||!group.options.some(option=>option.item_id===choice.item_id&&option.version===choice.version))throw new Error('현재 선택 가능한 승인 정의를 다시 확인해 주세요.');
 const replaced=new Set(group.options.map(option=>option.item_id));
 return [...previous.filter(ref=>!replaced.has(ref.item_id)&&ref.item_id!==choice.item_id),{item_id:choice.item_id,version:choice.version}];
}

export function knowledgeChoicesComplete(knowledge:ChatKnowledge,selected:KnowledgeDefinitionRef[]):boolean {
 return knowledge.availability!=='unavailable'&&!knowledge.blocking&&(knowledge.clarifications??[]).every(group=>group.options.some(option=>selected.some(ref=>ref.item_id===option.item_id&&ref.version===option.version)));
}

export function originalKnowledgeQuestion(messages:ReadonlyArray<{role:string;text:string}>,answerIndex:number):string|null {
 for(let index=Math.min(answerIndex,messages.length)-1;index>=0;index--)if(messages[index].role==='user')return messages[index].text;
 return null;
}
