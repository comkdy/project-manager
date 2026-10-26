import type {AnalysisRun,ApprovedAnalysisDefinition} from './analyticsTypes.ts';
import type {CollectedDraft,KnowledgeQuestion} from './semanticCollectionTypes.ts';
import type {DefinitionRef} from './semanticTypes.ts';

export interface AnalysisSelection {workspace:string;userId:string;runId:string}
export interface AnalysisKnowledgeJourney extends AnalysisSelection {
 role:string;projectId:number|null;questionId:string;questionTitle:string;
 definition:DefinitionRef|null;
}
export interface AnalysisResume {workspace:string;userId:string;role:string;run:AnalysisRun;token:number}

export function selectedAnalysisRun(selection:AnalysisSelection|null,workspace:string,userId:string|undefined):string|null {
 return selection?.workspace===workspace&&selection.userId===userId?selection.runId:null;
}

export function analysisJourneyCurrent(journey:Pick<AnalysisKnowledgeJourney,'workspace'|'userId'|'role'>|null,workspace:string,userId:string|undefined,role:string|undefined){
 return !!journey&&journey.workspace===workspace&&journey.userId===userId&&journey.role===role;
}

export function analysisReturnValid(journey:AnalysisKnowledgeJourney,run:AnalysisRun){
 const question=run.questions.find(q=>q.id===journey.questionId);
 const definition=question?.approved_definition;
 return run.id===journey.runId&&(run.request.project_id??run.request._project_scope?.id??null)===journey.projectId&&!!question&&
  (!journey.definition||!!definition&&definition.item_id===journey.definition.item_id&&definition.version===journey.definition.version);
}

export function approvedAnalysisDefinitions(run:AnalysisRun):ApprovedAnalysisDefinition[]{
 const definitions=run.questions.flatMap(q=>q.approved_definition?[q.approved_definition]:[]);
 return definitions.filter((value,index)=>definitions.findIndex(d=>d.item_id===value.item_id&&d.version===value.version)===index);
}

export function questionDraftTarget(question:KnowledgeQuestion,admin:boolean){
 const draft=question.draft;
 return admin&&question.status==='review'&&!question.dataset_changed&&draft?.status==='pending'&&draft.question_id===question.id&&
  question.answers?.some(answer=>answer.id===draft.answer_id&&answer.status==='ready')?draft:null;
}

export function exactQuestionDraft(questionId:string,answerId:string,draft:CollectedDraft){
 return draft.question_id===questionId&&draft.question.id===questionId&&draft.answer_id===answerId;
}

export function createAnalysisRestoreGuard(){
 let generation=0;
 return {
  start:(runId:string)=>({runId,generation:++generation}),
  invalidate:()=>{generation++},
  accepts:(ticket:{runId:string;generation:number},selected:string|null,visible:boolean)=>visible&&ticket.runId===selected&&ticket.generation===generation,
 };
}
