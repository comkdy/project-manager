import {useEffect,useId,useMemo,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {loadAnalysisRun} from './analyticsApi';
import type {AnalysisPoint,AnalysisRequest,AnalysisResultSeries,AnalysisRun} from './analyticsTypes';
import type {ChatAnalysis} from './chatHistory';
import './chat-analysis.css';

const DAY=86400000;
const number=(value:number|null|undefined)=>value==null||!Number.isFinite(value)?'—':value.toLocaleString('ko-KR',{maximumSignificantDigits:8});
const difference=(value:number|null|undefined)=>value==null?'—':`${value>0?'+':''}${number(value)}`;
const seriesName=(s:AnalysisResultSeries)=>[s.family==='epm'?'EPM':'Inline',s.operation,s.parameter].filter(Boolean).join(' · ');
const seriesKey=(s:AnalysisResultSeries)=>JSON.stringify([s.family,s.operation??'',s.parameter]);
const day=(value:string)=>new Date(value).toISOString().slice(0,10);
const dateTime=(value?:string)=>value?new Date(value).toISOString().replace('T',' ').replace(/:00\.000Z$/,'').replace(/\.000Z$/,''):'미정';
const statuses={needs_criteria:'변화 판정 기준 확인 필요',insufficient_data:'유효 측정 수 부족 또는 평균 계산 불가',changed:'지정한 변화량 기준에 해당',stable:'지정한 변화량 기준 미만'};

/** Fetch existing results only when the answer enters the chat viewport.
 * No recalculation/model call and no duplicate time series in chat storage.
 */
export default function ChatAnalysisAnswer({analysis,text,workspace}:{analysis:ChatAnalysis;text:string;workspace:string}){
 const root=useRef<HTMLDivElement>(null),[visible,setVisible]=useState(false),[selected,setSelected]=useState('');
 const supported=!!analysis.run_id&&['trend','split'].includes(analysis.request?.kind??'')&&analysis.status!=='unavailable';
 useEffect(()=>{
  if(!supported||!root.current)return;
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setVisible(true);observer.disconnect()}},{rootMargin:'160px'});
  observer.observe(root.current);return()=>observer.disconnect();
 },[supported]);
 const detail=useQuery({queryKey:['analytics','run',workspace,analysis.run_id],queryFn:()=>loadAnalysisRun(analysis.run_id!),enabled:supported&&visible,
  staleTime:30000,retry:false,refetchInterval:q=>['queued','running','cancelling'].includes(q.state.data?.status??'')?2000:false});
 const run=detail.data;
 const series=useMemo(()=>{
  const values=run?.result?.series??[];
  if(run?.kind!=='split')return values;
  return values.filter(s=>(!analysis.display?.family||s.family===analysis.display.family)&&(!analysis.display?.parameter||s.parameter===analysis.display.parameter));
 },[run,analysis.display?.family,analysis.display?.parameter]);
 const shown=series.slice(0,20),active=shown.find(s=>seriesKey(s)===selected)??shown[0];
 const ready=!!run?.result&&!!active&&!detail.isError&&['trend','split'].includes(run.kind);
 return <div ref={root} className="chat-analysis-answer">
  {ready?<>
   <section className="chat-analysis-visual" aria-label={run.kind==='trend'?'추세 분석 그래프':'Split 비교표'}>
    <div className="chat-analysis-heading"><strong>{run.kind==='trend'?'일별 평균 추세':'Recipe split 비교'}</strong><span>{run.request._project_scope?.name??'전체 프로젝트'}</span></div>
    {run.status==='needs_input'&&<p className="chat-analysis-notice">처리 기준 확인이 필요한 임시 집계입니다.</p>}
    {run.kind==='trend'?<>
     {shown.length>1?<label className="chat-analysis-select">분석 PARA<select aria-label="그래프로 볼 PARA" value={seriesKey(active)} onChange={e=>setSelected(e.target.value)}>{shown.map(s=><option value={seriesKey(s)} key={seriesKey(s)}>{seriesName(s)}</option>)}</select></label>:<h4>{seriesName(active)}</h4>}
     <TrendPlot key={seriesKey(active)} series={active} request={run.request}/>
     <div className="chat-analysis-metrics">
      <Metric label="기준 평균" value={number(active.baseline?.mean)} note={`유효 ${number(active.baseline?.valid_count)}건`}/>
      <Metric label="비교 평균" value={number(active.current?.mean)} note={`유효 ${number(active.current?.valid_count)}건`}/>
      <Metric label="평균 차이" value={difference(active.delta)} note="비교 − 기준"/>
     </div>
     <Status series={active}/>
    </>:<SplitTable run={run} series={shown}/>}
    {series.length>shown.length&&<p className="chat-analysis-note">전체 {series.length.toLocaleString()}개 중 {shown.length}개를 표시합니다. 나머지는 상세 분석에서 확인하세요.</p>}
    <AnalysisConditions run={run}/>
    {!!run.result?.warnings?.length&&<ul className="chat-analysis-notes">{run.result.warnings.map(warning=><li key={warning}>{warning}</li>)}</ul>}
   </section>
   <details className="chat-analysis-explanation"><summary>답변 설명 전체 보기</summary><p>{text}</p></details>
  </>:<>
   <p>{text}</p>
   {run?.status==='cancelling'&&<p role="status">분석 취소 요청을 확인 중입니다.</p>}
   {run?.status==='cancelled'&&<p role="status">분석이 취소되었습니다. 작업 상태 화면에서 다시 시도할 수 있습니다.</p>}
   {supported&&detail.isFetching&&!run&&<p className="chat-analysis-loading" role="status">{analysis.request?.kind==='trend'?'추세 그래프':'비교표'}를 불러오고 있습니다…</p>}
   {supported&&detail.isError&&<div className="chat-analysis-notice" role="status">분석 시각화를 불러오지 못했습니다. <button onClick={()=>void detail.refetch()}>다시 불러오기</button></div>}
  </>}
 </div>;
}

function Metric({label,value,note}:{label:string;value:string;note:string}){
 return <div><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}
function Status({series}:{series:AnalysisResultSeries}){
 return series.status?<p className="chat-analysis-status">{statuses[series.status]}</p>:null;
}
function AnalysisConditions({run}:{run:AnalysisRun}){
 const q=run.request;
 return <details className="chat-analysis-conditions"><summary>분석 기간·조건 확인</summary><dl>
  {run.kind==='trend'?<><dt>기준 기간</dt><dd>{dateTime(q.baseline_start)} ~ {dateTime(q.baseline_end)}</dd><dt>비교 기간</dt><dd>{dateTime(q.current_start)} ~ {dateTime(q.current_end)}</dd></>:<><dt>분석 기간</dt><dd>{dateTime(q.start)} ~ {dateTime(q.end)}</dd><dt>Recipe 공정</dt><dd>{q.operation}</dd></>}
  <dt>시간 기준</dt><dd>UTC · 시작 포함, 종료 미포함</dd>
  <dt>재측정</dt><dd>{q.retest_policy==='latest_per_site'?'각 기간·위치의 마지막 측정, 마지막 NULL 유지':'모든 측정 포함'}</dd>
  {q._project_scope&&<><dt>프로젝트 범위</dt><dd>{q._project_scope.lot_cds.join(', ')}</dd></>}
 </dl></details>;
}

function SplitTable({run,series}:{run:AnalysisRun;series:AnalysisResultSeries[]}){
 const a=run.request.recipe_a??'그룹 A',b=run.request.recipe_b??'그룹 B',single=series.length===1?series[0]:null;
 return <>
  <p className="chat-analysis-subtitle">{run.request.operation} · {a} / {b}</p>
  {single?<>
   <h4>{seriesName(single)}</h4>
   <div className="chat-analysis-table-wrap" tabIndex={0} role="region" aria-label="Recipe별 비교 수치"><table className="chat-analysis-table"><caption>{a} · {b} 비교</caption><thead><tr><th scope="col">항목</th><th scope="col">{a}</th><th scope="col">{b}</th></tr></thead><tbody>
    <tr><th scope="row">평균</th><td>{number(single.group_a?.mean)}</td><td>{number(single.group_b?.mean)}</td></tr>
    <tr><th scope="row">유효 측정</th><td>{number(single.group_a?.valid_count)}건</td><td>{number(single.group_b?.valid_count)}건</td></tr>
    <tr><th scope="row">결측</th><td>{number(single.group_a?.missing_count)}건</td><td>{number(single.group_b?.missing_count)}건</td></tr>
    <tr><th scope="row">Wafer 수</th><td>{number(single.group_a?.wafer_count)}</td><td>{number(single.group_b?.wafer_count)}</td></tr>
   </tbody></table></div>
   <div className="chat-analysis-delta"><span>평균 차이 <small>{b} − {a}</small></span><strong>{difference(single.delta)}</strong></div>
   <Status series={single}/>
  </>:<div className="chat-analysis-table-wrap" tabIndex={0} role="region" aria-label="PARA별 split 비교 수치"><table className="chat-analysis-table multi"><caption>평균 · 유효 측정 수 / 차이: {b} − {a}</caption><thead><tr><th scope="col">PARA</th><th scope="col">{a}</th><th scope="col">{b}</th><th scope="col">평균 차이</th></tr></thead><tbody>{series.map(s=><tr key={seriesKey(s)}><th scope="row">{seriesName(s)}{s.status&&<small>{statuses[s.status]}</small>}</th><td>{number(s.group_a?.mean)}<small>{number(s.group_a?.valid_count)}건</small></td><td>{number(s.group_b?.mean)}<small>{number(s.group_b?.valid_count)}건</small></td><td>{difference(s.delta)}</td></tr>)}</tbody></table></div>}
  {!!run.result?.excluded_ambiguous_wafer_count&&<p className="chat-analysis-notice">Recipe 대응이 모호한 wafer {run.result.excluded_ambiguous_wafer_count}개를 제외한 임시 집계입니다.</p>}
 </>;
}

function TrendPlot({series,request}:{series:AnalysisResultSeries;request:AnalysisRequest}){
 const id=useId(),[selected,setSelected]=useState<number|null>(null);
 const points=useMemo(()=>(series.points??[]).filter(p=>Number.isFinite(Date.parse(p.at))).slice().sort((a,b)=>Date.parse(a.at)-Date.parse(b.at)),[series.points]);
 const values=points.filter((p):p is AnalysisPoint&{mean:number}=>p.mean!=null&&Number.isFinite(p.mean));
 if(!values.length)return <p className="chat-analysis-empty">해당 기간에 그래프로 표시할 유효 측정값이 없습니다.</p>;
 const width=370,height=210,left=58,right=12,top=16,bottom=34;
 const low=Math.min(...values.map(p=>p.mean)),high=Math.max(...values.map(p=>p.mean)),pad=(high-low)*.12||Math.abs(high)*.03||1,min=low-pad,max=high+pad;
 const boundaries=[request.baseline_start,request.baseline_end,request.current_start,request.current_end].filter((v):v is string=>!!v).map(Date.parse).filter(Number.isFinite);
 const first=Math.min(Date.parse(points[0].at),...boundaries),last=Math.max(Date.parse(points[points.length-1].at)+DAY,...boundaries);
 const x=(at:number)=>left+(at-first)/(last-first||DAY)*(width-left-right),y=(value:number)=>top+(max-value)/(max-min)*(height-top-bottom);
 let path='',previous:number|null=null;
 for(const p of points){const at=Date.parse(p.at);if(p.mean==null||!Number.isFinite(p.mean)){previous=null;continue}path+=`${previous==null||at-previous>DAY*1.5?'M':'L'}${x(at)},${y(p.mean)} `;previous=at}
 const focus=points[selected??points.length-1];
 function move(clientX:number,rect:DOMRect){const at=first+((clientX-rect.left)/rect.width*width-left)/(width-left-right)*(last-first);let nearest=0;for(let i=1;i<points.length;i++)if(Math.abs(Date.parse(points[i].at)-at)<Math.abs(Date.parse(points[nearest].at)-at))nearest=i;setSelected(nearest)}
 return <figure className="chat-trend-plot">
  <figcaption><span><i className="baseline"/>기준 기간</span><span><i className="current"/>비교 기간</span><small>일별 평균 · UTC</small></figcaption>
  <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={id} onPointerMove={e=>move(e.clientX,e.currentTarget.getBoundingClientRect())} onPointerLeave={()=>setSelected(null)}>
   <title id={id}>{seriesName(series)} 일별 평균 추세. {day(new Date(first).toISOString())}부터 {day(new Date(last).toISOString())}까지. 측정이 없는 날과 결측은 선으로 연결하지 않습니다.</title>
   {[[request.baseline_start,request.baseline_end,'#eef5f8'],[request.current_start,request.current_end,'#e8f4ef']].map(([start,end,color],index)=>start&&end?<rect key={index} x={x(Date.parse(start))} y={top} width={Math.max(0,x(Date.parse(end))-x(Date.parse(start)))} height={height-top-bottom} fill={color}/>:null)}
   {[0,.5,1].map(r=>{const v=min+(max-min)*r;return <g key={r}><line x1={left} x2={width-right} y1={y(v)} y2={y(v)} stroke="#dce5e9" strokeDasharray="3 3"/><text x={left-7} y={y(v)+3} textAnchor="end">{v.toLocaleString('ko-KR',{maximumSignificantDigits:4})}</text></g>})}
   <path d={path} fill="none" stroke="#267c86" strokeWidth="2"/>
   {values.map(p=><circle key={p.at} cx={x(Date.parse(p.at))} cy={y(p.mean)} r={points.length>120?1:2.5} fill="#267c86"/>)}
   {selected!==null&&<line x1={x(Date.parse(focus.at))} x2={x(Date.parse(focus.at))} y1={top} y2={height-bottom} stroke="#79939e" strokeDasharray="3 3"/>}
   <text x={left} y={height-13}>{day(new Date(first).toISOString()).slice(5)}</text><text x={width-right} y={height-13} textAnchor="end">{day(new Date(last).toISOString()).slice(5)}</text>
  </svg>
  <div className="chat-trend-readout"><span>{day(focus.at)}</span><strong>{number(focus.mean)}</strong><small>유효 {number(focus.valid_count)}건 · 결측 {number(focus.missing_count)}건</small></div>
  <input type="range" aria-label={`${seriesName(series)} 일별 수치 선택`} aria-valuetext={`${day(focus.at)}, 평균 ${number(focus.mean)}, 유효 ${number(focus.valid_count)}건`} min={0} max={points.length-1} value={selected??points.length-1} onChange={e=>setSelected(Number(e.target.value))}/>
 </figure>;
}
