import type {ScheduleView} from './analysisSchedules.generated';
const stamp=(value:string|null|undefined)=>value?new Date(value).toLocaleString('ko-KR'):'—';
export default function Periods({request}:{request:ScheduleView['request']}){
 return <dl className="schedule-periods">{request.kind==='trend'?<><div><dt>기준 기간</dt><dd>{stamp(request.baseline_start)} ~ {stamp(request.baseline_end)}</dd></div><div><dt>비교 기간</dt><dd>{stamp(request.current_start)} ~ {stamp(request.current_end)}</dd></div></>:<div><dt>분석 기간</dt><dd>{stamp(request.start)} ~ {stamp(request.end)}</dd></div>}
 <div><dt>분석 대상</dt><dd>{[request.family?.toUpperCase(),request.operation,request.parameter].filter(Boolean).join(' · ')||'전체 PARA'}{request.recipe_a&&` · ${request.recipe_a} / ${request.recipe_b}`}</dd></div>
 {request.retest_policy&&<div><dt>재측정</dt><dd>{request.retest_policy==='all_events'?'모든 측정':'마지막 측정'}</dd></div>}
 {request.kind!=='statistics'&&<div><dt>판정 기준</dt><dd>최소 유효 표본 {request.min_samples??'PARA별'} · 평균 변화량 {request.absolute_delta??'PARA별'}{(request.series_criteria?.length??0)>0&&` · PARA별 기준 ${request.series_criteria!.length}개`}</dd></div>}
 {(request.series_criteria?.length??0)>0&&<div><dt>PARA별 기준 상세</dt><dd>{request.series_criteria!.map(c=><span className="schedule-rule" key={`${c.family}:${c.operation}:${c.parameter}`}>{c.family.toUpperCase()} {c.operation} {c.parameter} · 최소 표본 {c.min_samples} · 변화량 {c.absolute_delta}</span>)}</dd></div>}
 <div><dt>승인 근거</dt><dd>{request.knowledge_refs?.length??0}개 · 원래 분석에 연결된 버전 유지</dd></div></dl>;
}
