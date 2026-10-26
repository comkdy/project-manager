import type {CollectedDraft} from './semanticCollectionTypes.ts';
import type {ReviewInput} from './semanticDrafts.ts';
import type {KnowledgeItem} from './semanticTypes.ts';

export interface DraftReviewState extends ReviewInput {base:CollectedDraft;latest:CollectedDraft;editRevision:number}
type Action={type:'receive';draft:CollectedDraft}|{type:'edit';value:Partial<ReviewInput>}|{type:'restore';value:ReviewInput;revision:number}|{type:'rebase'}|{type:'reload'}|{type:'saved';draft:CollectedDraft};

export function initialDraftReview(draft:CollectedDraft):DraftReviewState {
 return {base:draft,latest:draft,editRevision:draft.revision,proposal:draft.proposal,unresolved:draft.proposal.content.unresolved.join('\n'),note:'',target:'',targetRevision:null,dirty:false};
}

export function draftReviewReducer(state:DraftReviewState,action:Action):DraftReviewState {
 if(action.type==='edit')return {...state,...action.value};
 if(action.type==='restore')return {...state,...action.value,editRevision:action.revision,
  dirty:action.value.dirty||JSON.stringify(action.value.proposal)!==JSON.stringify(state.latest.proposal)};
 if(action.type==='rebase')return {...state,base:state.latest,editRevision:state.latest.revision,dirty:true};
 if(action.type==='receive')return action.draft.id!==state.base.id||action.draft.question_id!==state.base.question_id||action.draft.answer_id!==state.base.answer_id||action.draft.revision<state.latest.revision?state:{...state,latest:action.draft};
 if(action.type==='reload')return initialDraftReview(state.latest);
 // Only our successful save or an explicit reload replaces local input.
 const saved=initialDraftReview(action.draft);
 return state.latest.revision>saved.latest.revision?{...saved,latest:state.latest}:saved;
}

export const draftReviewChanged=(state:DraftReviewState)=>state.latest.revision!==state.editRevision;
// Persist only the editor's fields; answer evidence and server records stay in memory.
export const draftReviewInput=(state:DraftReviewState):ReviewInput=>({proposal:state.proposal,unresolved:state.unresolved,note:state.note,target:state.target,targetRevision:state.targetRevision,dirty:state.dirty});
export const draftTargetChanged=(state:ReviewInput,items:KnowledgeItem[])=>!!state.target&&!items.some(i=>i.id===state.target&&i.revision===state.targetRevision&&i.kind===state.proposal.kind&&i.scope===state.proposal.scope);
