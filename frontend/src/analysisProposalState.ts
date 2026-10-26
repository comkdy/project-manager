import type {AnalysisProposalConfirm,AnalysisProposalView} from './analysisProposal.generated.ts';
import type {AnalysisRequest} from './analyticsTypes.ts';

export interface AnalysisProposalSelection {
 workspace:string;userId:string;role:string;proposal:AnalysisProposalView;token:number;
}

export function proposalEditorRequest(proposal:AnalysisProposalView):AnalysisRequest|null {
 if(!proposal.request)return null;
 const request=Object.fromEntries(Object.entries(proposal.request).filter(([,value])=>value!==null&&value!==undefined)) as AnalysisRequest;
 if(request.series_criteria)request.series_criteria=request.series_criteria.map(row=>({...row,operation:row.operation??''}));
 return {...request,...(proposal.project_scope?{_project_scope:proposal.project_scope}:{})};
}

export function proposalConfirmBody(proposal:AnalysisProposalView,edited:AnalysisRequest):AnalysisProposalConfirm {
 if(proposal.status!=='pending'||!proposal.request||!proposal.revision||!proposal.workspace_token)throw new Error('현재 조건안을 다시 확인해 주세요.');
 const {_project_scope,...request}=edited;
 // Statistics and multi-PARA changes have no target picker. Keep their scope.
 const fixedTarget=proposal.request.kind==='statistics'||proposal.request.kind==='trend'&&!proposal.request.parameter;
 const target=fixedTarget?Object.fromEntries(['family','operation','parameter'].map(key=>[key,proposal.request![key as 'family'|'operation'|'parameter']])):{};
 return {expected_revision:proposal.revision,workspace_token:proposal.workspace_token,project_id:proposal.project_id??null,
  request:{...request,...target,kind:proposal.request.kind,purpose:proposal.request.purpose??'analysis',project_id:proposal.project_id??null,bucket:proposal.request.bucket??'day'}};
}

export function proposalFixedTargetLabel(proposal:AnalysisProposalView|null):string|null {
 const request=proposal?.request;
 if(!request||request.kind==='split'||request.kind==='trend'&&request.parameter)return null;
 return [request.family==='inline'?'Inline':request.family==='epm'?'EPM':'Inline · EPM',request.operation?`공정 ${request.operation}`:'전체 공정',request.parameter??'전체 PARA'].join(' · ');
}

export function proposalRemainingBlockers(proposal:AnalysisProposalView|null,periodComplete:boolean):string[] {
 // Target/scope/method require server catalog validation; never declare them resolved here.
 return (proposal?.blockers??[]).filter(value=>value!=='period'||!periodComplete);
}

export function proposalReadyToConfirm(base:AnalysisProposalView|null,latest:AnalysisProposalView|undefined,workspace:string,query:{isSuccess:boolean;isFetching:boolean;isError:boolean}):boolean {
 return !!base&&!!latest&&query.isSuccess&&!query.isFetching&&!query.isError&&base.status==='pending'&&latest.status==='pending'&&
  !!base.request&&!!base.revision&&base.workspace_token===workspace&&latest.workspace_token===workspace&&
  latest.proposal_id===base.proposal_id&&latest.revision===base.revision&&(latest.project_id??null)===(base.project_id??null);
}

export const analysisConditionNames:Record<string,string>={
 period:'분석 기간',retest_policy:'재측정 처리',change_criteria:'변화 판정 기준',split_groups:'비교할 공정·recipe',
 analysis_target:'분석 대상',analysis_scope:'추가 조회 범위',analysis_method:'분석 방법',recipe_crossover:'재방문 wafer 처리',
 start:'시작 시각',end:'종료 시각',baseline_start:'기준 시작',baseline_end:'기준 종료',current_start:'비교 시작',current_end:'비교 종료',
 min_samples:'최소 유효 표본 수',absolute_delta:'평균 변화량 절댓값',
};
