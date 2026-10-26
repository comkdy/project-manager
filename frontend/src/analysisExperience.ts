import type {AnalysisRun,AnalysisResultSeries} from './analyticsTypes';
export type AnalysisFocus='epm'|'inline';
export type AnalysisStart={focus:AnalysisFocus;projectId:number|null;token:number};
export const analysisPaths={
 epm:{title:'Inline·Recipe와 EPM의 관계',question:'Inline과 Recipe에 따라 EPM이 어떻게 달라졌나요?',description:'Recipe별 EPM 차이를 먼저 보고, 같은 비교군에서 관측된 Inline 변화도 함께 살펴봅니다.'},
 inline:{title:'Recipe에 따른 Inline 변화',question:'Recipe를 바꾸면 Inline이 어떻게 달라지나요?',description:'공정의 두 Recipe를 비교하고 Inline 평균과 변동 폭을 확인합니다.'},
};
export function runProject(run:AnalysisRun){return run.request.project_id??run.request._project_scope?.id??null;}
export function focusForRun(run:AnalysisRun):AnalysisFocus{return run.request.family==='inline'?'inline':'epm';}
export function focusSeries(run:AnalysisRun,focus:AnalysisFocus){return (run.result?.series??[]).filter(s=>s.family===focus).sort((a,b)=>Number(b.status==='changed')-Number(a.status==='changed')||`${a.operation??''}:${a.parameter}`.localeCompare(`${b.operation??''}:${b.parameter}`,'ko'));}
export function comparisonValues(series:AnalysisResultSeries){const a=series.group_a??series.baseline,b=series.group_b??series.current;return {a,b,comparable:!!a&&!!b&&a.valid_count>0&&b.valid_count>0&&Number.isFinite(a.mean)&&Number.isFinite(b.mean)};}
export function focusCounts(series:AnalysisResultSeries[]){return {total:series.length,changed:series.filter(s=>s.status==='changed').length,unconfirmed:series.filter(s=>s.status==='needs_criteria').length,insufficient:series.filter(s=>s.status==='insufficient_data'||(!s.statistics&&!comparisonValues(s).comparable)).length};}
export function resultTitle(run:AnalysisRun){return run.kind==='split'?`${run.request.operation??'공정'} · ${run.request.recipe_a??'기준 Recipe'} / ${run.request.recipe_b??'평가 Recipe'}`:run.request.parameter?`${run.request.family==='inline'?'Inline':'EPM'} · ${run.request.parameter}`:run.kind==='statistics'?'측정 현황':'기간별 변화';}
