import type {AnalysisRun,AnalysisResultSeries} from './analyticsTypes';
import {comparisonValues,focusCounts,focusSeries,type AnalysisFocus} from './analysisExperience';
const number=(n:number|null|undefined)=>n==null||!Number.isFinite(n)?'—':n.toLocaleString('ko-KR',{maximumSignificantDigits:6});
function ChangeCard({series}:{series:AnalysisResultSeries}){
 const {a,b,comparable}=comparisonValues(series),stats=series.statistics;
 return <article className={`impact-card ${series.status==='changed'?'is-changed':''}`}><small>{series.operation||series.family.toUpperCase()}</small><h3>{series.parameter}</h3>{stats?<><p>평균 <strong>{number(stats.mean)}</strong></p><p>변동 폭(표준편차) {number(stats.stddev)}</p><small>{stats.lot_count} Lot · {stats.wafer_count} Wafer</small></>:<><p>평균 <strong>{number(a?.mean)} → {number(b?.mean)}</strong></p><p>{comparable?`차이 ${number(series.delta)} · 변동 폭(표준편차) ${number(a?.stddev)} → ${number(b?.stddev)}`:'비교할 유효 측정값이 부족합니다.'}</p><small>기준 {a?.lot_count??0} Lot · {a?.wafer_count??0} Wafer / 비교 {b?.lot_count??0} Lot · {b?.wafer_count??0} Wafer</small><p className="impact-verdict">{series.status==='changed'?'설정한 변화 기준을 넘었습니다.':series.status==='stable'?'설정한 변화 기준 미만입니다.':series.status==='insufficient_data'?'판정에 필요한 측정 수가 부족합니다.':'판정 기준 없이 관측된 차이를 표시합니다.'}</p></>}</article>;
}
export default function AnalysisBrief({run,focus}:{run:AnalysisRun;focus:AnalysisFocus}){
 if(!run.result)return null;
 const primary=focusSeries(run,focus),counts=focusCounts(primary),inline=focus==='epm'&&run.kind==='split'?focusSeries(run,'inline'):[];
 return <section className="impact-brief" aria-label="변화 요약"><h2>{focus==='epm'?'EPM에서 무엇이 달라졌나요?':'Inline에서 무엇이 달라졌나요?'}</h2><p>{primary.length?`${counts.total}개 항목${run.kind==='statistics'?'의 측정 현황입니다.':` 중 설정한 변화 기준을 넘은 항목 ${counts.changed}개 · 기준 미정 ${counts.unconfirmed}개 · 비교·표본 부족 ${counts.insufficient}개입니다.`}`:'이 결과에는 선택한 측정 종류가 없습니다. 아래에서 전체 결과를 확인할 수 있습니다.'}</p>{run.kind==='split'&&<p>기준 {run.request.recipe_a} → 비교 {run.request.recipe_b} · {run.request.operation}</p>}
 <div className="impact-cards">{primary.slice(0,3).map(s=><ChangeCard key={`${s.operation}:${s.parameter}`} series={s}/>)}</div>{primary.length>3&&<p>변화 기준을 넘은 항목부터, 같은 판정 안에서는 이름순으로 3개를 표시합니다. 전체 {primary.length}개는 아래 상세 비교에서 볼 수 있습니다.</p>}
 {inline.length>0&&<details className="impact-related"><summary>같은 Recipe 비교군의 Inline도 함께 보기 ({inline.length}개)</summary><p>각 항목을 같은 Recipe 기준으로 나눈 결과입니다. Inline과 EPM의 개별 측정값을 짝지은 상관·원인 분석은 아직 포함하지 않습니다.</p><div className="impact-cards">{inline.slice(0,3).map(s=><ChangeCard key={`${s.operation}:${s.parameter}`} series={s}/>)}</div>{inline.length>3&&<p>나머지 항목은 ‘전체 측정’ 상세 비교에서 확인하세요.</p>}</details>}
 <p className="impact-next"><strong>다음 확인</strong> {run.kind==='split'?'평가 Lot와 처리 시점이 비교 가능한지 확인하세요. 이 차이만으로 Recipe 또는 장비가 원인이라고 확정하지 않습니다.':'변화한 항목의 Recipe 비교로 이어가거나, 연결된 질문에서 필요한 업무 기준을 확인하세요.'}</p>
 </section>;
}
