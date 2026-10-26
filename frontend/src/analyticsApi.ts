import {api,post} from './api';
import type {AnalysisCatalog,AnalysisRequest,AnalysisRun} from './analyticsTypes';

export const loadAnalysisCatalog=()=>api<AnalysisCatalog>('/analytics/catalog');
export const loadAnalysisRuns=()=>api<AnalysisRun[]>('/analytics/runs');
export const loadAnalysisRun=(id:string)=>api<AnalysisRun>(`/analytics/runs/${encodeURIComponent(id)}`);
export const createAnalysisRun=(request:AnalysisRequest)=>post<AnalysisRun>('/analytics/runs',request);
