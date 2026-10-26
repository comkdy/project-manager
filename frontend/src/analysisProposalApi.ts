import {api,post} from './api';
import type {AnalysisProposalConfirm,AnalysisProposalView} from './analysisProposal.generated';
import type {AnalysisRun} from './analyticsTypes';

export const loadAnalysisProposal=(id:string,project:number|null,signal?:AbortSignal)=>api<AnalysisProposalView>(`/analytics/proposals/${encodeURIComponent(id)}${project===null?'':`?project_id=${project}`}`,{signal});
export const confirmAnalysisProposal=(id:string,body:AnalysisProposalConfirm)=>post<AnalysisRun>(`/analytics/proposals/${encodeURIComponent(id)}/confirm`,body);
