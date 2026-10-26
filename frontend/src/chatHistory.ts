import type {ChatKnowledge} from './chatKnowledge.generated';
import type {AnalysisProposalView} from './analysisProposal.generated';
import type {ChatEvaluationCases} from './evaluationCases';
import type {ChatDataAnalysis} from './chatDataAnalysis';
import {api} from './api';
import type {CardSpec,ISOWeek,Proposal} from './types';
import type {AnalysisQuestion,AnalysisRequest,AnalysisResultSeries} from './analyticsTypes';

export interface ChatAnalysis {
 run_id?:string;status:string;questions?:AnalysisQuestion[];source_watermark?:{captured_at:string}|null;
 request?:AnalysisRequest;series?:AnalysisResultSeries[];
 display?:{family?:string|null;parameter?:string|null;shown:number;total:number};
}

export interface ChatMessage {
 id?:string;role:'user'|'assistant';text:string;proposal?:Proposal;options?:string[];analysis?:ChatAnalysis;
 analysis_proposal?:AnalysisProposalView;evaluation_cases?:ChatEvaluationCases;knowledge?:ChatKnowledge;applied?:boolean;restored?:boolean;status?:string;created_at?:string;
 data_analysis?:ChatDataAnalysis;
}
export interface ConversationSummary {id:string;title:string;project_id:number|null;revision:number;created_at:string;updated_at:string}
export interface ConversationContext {project_id:number|null;period_start?:ISOWeek|null;dataset_id?:string|null;card?:CardSpec|null}
export interface SavedConversation extends ConversationSummary {
 context:ConversationContext;messages:ChatMessage[];running:boolean;next_before:number|null;
}
export interface ConversationPage {items:ConversationSummary[];next_offset:number|null}
export function conversations(project:number|null,search:string,offset=0,signal?:AbortSignal){
 const query=new URLSearchParams({search,offset:String(offset)});
 if(project!==null)query.set('project_id',String(project));
 return api<ConversationPage>(`/chat/conversations?${query}`,{signal});
}
export function conversation(id:string,before?:number){
 return api<SavedConversation>(`/chat/conversations/${encodeURIComponent(id)}${before?`?before=${before}`:''}`);
}
