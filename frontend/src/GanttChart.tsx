import {useEffect,useId,useLayoutEffect,useMemo,useRef,useState} from 'react';
import type {PointerEvent as ReactPointerEvent,KeyboardEvent as ReactKeyboardEvent} from 'react';
import {Alert,Button,IconButton} from '@mui/material';
import {ChevronDown,Clock3,RotateCcw,Search,X} from 'lucide-react';
import {useQuery} from '@tanstack/react-query';
import {ApiError,requestChart} from './api';
import {chartQueryOptions,dashboardRefreshOptions} from './dashboardRefresh';
import {ErrorNotice,Loading} from './components';
import {operationIntervals,ribbonSegments} from './ganttIntervals';
import {flagLabel,layoutKeyFlags} from './ganttFlags';
import type {CardSpec,ChartResult,QueryContext} from './types';
import './gantt.css';

export interface GanttRow {displayed?:boolean;key_operation?:boolean;main_oper_yn?:boolean;lot_cd:string;lot_id:string;operation:string;operation_seq:number;status:'completed'|'current'|'upcoming';start_at:string|null;end_at:string|null;current_expected_end:string|null;actual_completed_at:string|null;expected_hours:number|null;interval_expected_hours?:number|null;tat_samples:number;tat_basis:string;estimated:boolean;overrun:boolean;issue:string|null;remark:string|null}
type GanttInterval=ReturnType<typeof operationIntervals<GanttRow>>[number];
const time=(value:string|null)=>value?new Date(value).getTime():null;
const stamp=(value:string|null)=>value?new Date(value).toLocaleString('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'미정';
const intervalStamp=(value:number|null)=>stamp(value===null?null:new Date(value).toISOString());
const hours=(value:number|null)=>value?.toLocaleString('ko-KR',{maximumFractionDigits:4})??'미정';
const names={completed:'완료',current:'진행 중',upcoming:'예상'};
const HOUR=3600000;
type LotSummary={lot_cd:string;lot_id:string;total_steps:number;completed_steps:number;displayed_steps:number;current_operation:string;plan_end_at?:string|null;ribbon?:{start:string;end:string;status:GanttRow['status']}[]};

export function GanttView({result,onPage,page=0,search='',onSearch,onFocusLot}:{result:ChartResult;onPage?:(n:number)=>void;page?:number;search?:string;onSearch?:(value:string)=>void;onFocusLot?:(value:string)=>void}){
 const rows=result.rows as unknown as GanttRow[],asOf=String(result.meta.as_of),base=time(asOf)!;
 const summaries=(result.meta.lots??[]) as LotSummary[];
 const scrollRef=useRef<HTMLDivElement>(null),[width,setWidth]=useState(600),labelClip=useId().replaceAll(':','');
 useLayoutEffect(()=>{
  const element=scrollRef.current;if(!element)return;
  const measure=()=>{if(element.clientWidth>0)setWidth(element.clientWidth)};
  const observer=new ResizeObserver(measure);observer.observe(element);measure();
  return()=>observer.disconnect();
 },[]);
 const [zoom,setZoom]=useState<'focus'|'day'|'week'|'plan'>('day'),[offset,setOffset]=useState(0),[selected,setSelected]=useState<GanttRow|null>(null),[stepPage,setStepPage]=useState(0);
 useEffect(()=>{setZoom('day');setOffset(0);setSelected(null)},[result.meta.detail_lot]);
 const summaryFor=(r:GanttRow)=>summaries.find(s=>s.lot_cd===r.lot_cd&&s.lot_id===r.lot_id);
 const focus=(lot:string)=>{setSelected(null);setOffset(0);onFocusLot?.(lot)};
 const visibleStart=result.meta.visible_start?new Date(`${result.meta.visible_start}T00:00:00`).getTime():-Infinity;
 const starts=[...rows.map(r=>time(r.start_at)??time(r.actual_completed_at)),...summaries.flatMap(s=>(s.ribbon??[]).map(b=>time(b.start)))].filter((v):v is number=>v!==null),ends=[...rows.map(r=>time(r.current_expected_end??r.end_at)),...summaries.map(r=>time(r.plan_end_at??null))].filter((v):v is number=>v!==null);
 const planStart=Math.max(Math.min(base-24*HOUR,...starts),visibleStart),planEnd=Math.max(base+24*HOUR,...ends);
 const span=zoom==='plan'?Math.max(24*HOUR,planEnd-planStart):({focus:12,day:72,week:168}[zoom])*HOUR;
 const start=(zoom==='plan'?planStart:Math.max(base-(zoom==='focus'?4:24)*HOUR,visibleStart))+offset;
 const grouped=useMemo(()=>{
  const groups=new Map<string,GanttRow[]>();for(const row of rows){const key=`${row.lot_cd}/${row.lot_id}`;if(!groups.has(key))groups.set(key,[]);groups.get(key)!.push(row)}return [...groups.values()];
 },[rows]);
 const intervals=useMemo(()=>grouped.map(route=>operationIntervals(route,base,summaryFor(route[0])?.plan_end_at??null)),[grouped,base,summaries]);
 const ribbons=useMemo(()=>intervals.map((route,i)=>summaryFor(grouped[i][0])?.ribbon?.map(b=>({...b,start:time(b.start)!,end:time(b.end)!}))??ribbonSegments(route)),[intervals,summaries]);
 const selectedInterval=selected?intervals.flat().find(interval=>interval.row===selected):undefined;
 const selectedRoute=selected?grouped.find(route=>route[0].lot_cd===selected.lot_cd&&route[0].lot_id===selected.lot_id):undefined;
 useEffect(()=>{setSelected(old=>old?rows.find(r=>r.lot_cd===old.lot_cd&&r.lot_id===old.lot_id&&r.operation_seq===old.operation_seq)??null:null)},[rows]);
 const hasFlags=rows.some(r=>r.key_operation),ribbonY=hasFlags?44:12;
 const left=Math.min(155,Math.max(96,Math.round(width*.28))),right=width-35,plotWidth=Math.max(1,right-left),rowHeight=hasFlags?88:60,height=48+grouped.length*rowHeight;
 const tickCount=Math.max(2,Math.min(7,Math.floor(plotWidth/85)+1));
 const px=(ms:number)=>left+(ms-start)/span*plotWidth;
 const pan=useRef<{pointerId:number;x:number;offset:number;scale:number;latest:number;moved:boolean}|null>(null);
 const panFrame=useRef<number|null>(null),suppressClick=useRef(false),[dragging,setDragging]=useState(false);
 useEffect(()=>()=>{if(panFrame.current!==null)cancelAnimationFrame(panFrame.current)},[]);
 const beginPan=(event:ReactPointerEvent<SVGSVGElement>)=>{
  if(event.button!==0||event.pointerType==='touch')return;
  const x=event.clientX-event.currentTarget.getBoundingClientRect().left;
  if(x<left||x>right)return;
  suppressClick.current=false;
  pan.current={pointerId:event.pointerId,x:event.clientX,offset,scale:span/plotWidth,latest:offset,moved:false};
  event.preventDefault();event.stopPropagation();
 };
 const movePan=(event:ReactPointerEvent<SVGSVGElement>)=>{
  const state=pan.current;if(!state||state.pointerId!==event.pointerId)return;
  const delta=event.clientX-state.x;
  if(!state.moved&&Math.abs(delta)<4)return;
  if(!state.moved){state.moved=true;suppressClick.current=true;setDragging(true);event.currentTarget.setPointerCapture(event.pointerId)}
  state.latest=state.offset-delta*state.scale;
  if(panFrame.current===null)panFrame.current=requestAnimationFrame(()=>{panFrame.current=null;if(pan.current)setOffset(pan.current.latest)});
  event.preventDefault();event.stopPropagation();
 };
 const endPan=(event:ReactPointerEvent<SVGSVGElement>)=>{
  const state=pan.current;if(!state||state.pointerId!==event.pointerId)return;
  if(panFrame.current!==null){cancelAnimationFrame(panFrame.current);panFrame.current=null}
  if(state.moved)setOffset(state.latest);
  pan.current=null;setDragging(false);
  if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
 };
 const panKeys=(event:ReactKeyboardEvent<SVGSVGElement>)=>{
  if(event.target!==event.currentTarget)return;
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();setOffset(v=>v+(event.key==='ArrowLeft'?-1:1)*span/4)}
  if(event.key==='Home'){event.preventDefault();setOffset(0)}
 };
 const panEvents={onPointerDown:beginPan,onPointerMove:movePan,onPointerUp:endPan,onPointerCancel:endPan,onLostPointerCapture:endPan,onKeyDown:panKeys};
 const ticks=Array.from({length:tickCount},(_,i)=>({x:left+i*plotWidth/(tickCount-1),date:new Date(start+i*span/(tickCount-1))}));
 const axisLabels=ticks.map(({x,date},i)=>{const detailed=zoom==='day'||zoom==='focus';return <text key={i} x={x} y={detailed?17:25} textAnchor={i===0?'start':i===tickCount-1?'end':'middle'} fill="#6b808b" fontSize="10">{date.toLocaleDateString('ko-KR',{month:'numeric',day:'numeric'})}{detailed&&<tspan x={x} dy="14">{date.toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false})}</tspan>}</text>});
 const choose=(r:GanttRow)=>{setSelected(r);const route=grouped.find(v=>v[0].lot_cd===r.lot_cd&&v[0].lot_id===r.lot_id)??[];setStepPage(Math.floor(Math.max(0,route.findIndex(v=>v.operation_seq===r.operation_seq))/50))};
 const colors={completed:'#acbcc5',current:'#087f80',upcoming:'#e4f4f1'};
 const describe=(interval:GanttInterval)=>{
  const r=interval.row,arrival=interval.start===null?'미정':new Date(interval.start).toLocaleString('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const tat=r.expected_hours==null?'미정':`${r.expected_hours.toLocaleString('ko-KR',{maximumFractionDigits:2})}시간`;
  return `공정명: ${r.operation}\n예상 도달 시간: ${arrival}\n예상 TAT: ${tat}`;
 };
 return <div className={`gantt-view ${dragging?'gantt-dragging':''}`} onClick={e=>e.stopPropagation()} onClickCapture={e=>{if(suppressClick.current){suppressClick.current=false;if(e.target instanceof Element&&e.target.closest('.gantt-svg,.gantt-axis-header')){e.preventDefault();e.stopPropagation()}}}}><div className="gantt-toolbar">
  {!!result.meta.detail_lot&&onFocusLot&&<Button size="small" onClick={()=>focus('')}>전체 랏</Button>}
  {onSearch&&<label className="gantt-search"><Search size={14} aria-hidden="true"/><input type="search" aria-label="랏 검색" placeholder="랏 검색" value={search} onChange={e=>onSearch(e.target.value)}/></label>}
  <div className="gantt-time-controls"><label className="gantt-range"><Clock3 size={14} aria-hidden="true"/><select aria-label="시간축" value={zoom} onChange={e=>{setZoom(e.target.value as 'focus'|'day'|'week'|'plan');setOffset(0)}}><option value="focus">12시간</option><option value="day">3일</option><option value="week">1주</option><option value="plan">전체</option></select><ChevronDown size={12} aria-hidden="true"/></label><IconButton className="gantt-reset" size="small" title="기준 시점으로 돌아가기" aria-label="기준 시점으로 돌아가기" disabled={Math.abs(offset)<1} onClick={()=>setOffset(0)}><RotateCcw size={14}/></IconButton></div>
 </div><div className="gantt-legend"><span><i className="completed"/>완료</span><span><i className="current"/>진행 중</span><span><i className="upcoming"/>예상</span><small>{result.meta.snapshot?'스냅샷':'조회 기준'} {stamp(asOf)}</small></div>
 <div className={`gantt-content ${selectedRoute?'has-detail':''}`}><div className="gantt-scroll" ref={scrollRef}><div className="gantt-axis-header"><svg width={width} height={48} viewBox={`0 0 ${width} 48`} role="group" aria-label="시간축 · 드래그하여 이동" tabIndex={0} {...panEvents}>{axisLabels}{base>=start&&base<=start+span&&<circle cx={px(base)} cy={37} r={3} fill="#087f80"/>}</svg><span style={{width:left}}>LOT / 현재 공정</span></div><div className="gantt-frozen-labels" style={{width:left}}><div className="gantt-frozen-header">LOT / 현재 공정</div>{grouped.map(route=><div className="gantt-frozen-row" style={{height:rowHeight}} key={`${route[0].lot_cd}/${route[0].lot_id}`}><strong>{route[0].lot_id}</strong><small>{summaryFor(route[0])?.current_operation??route.find(r=>r.status==='current')?.operation}{route.some(r=>r.overrun)?' · 소요 초과':''}</small>{summaryFor(route[0])&&<small>{summaryFor(route[0])!.completed_steps}/{summaryFor(route[0])!.total_steps} 단계 · 표시 {summaryFor(route[0])!.displayed_steps}</small>}</div>)}</div><svg className="gantt-svg" xmlns="http://www.w3.org/2000/svg" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-label="랏 공정 일정 · 드래그하여 시간 이동" tabIndex={0} {...panEvents} style={{fontFamily:'Segoe UI, Malgun Gothic, sans-serif',background:'#fff'}}>
  <defs><clipPath id={labelClip}><rect width={left-6} height={height}/></clipPath></defs><rect width={width} height={height} fill="#fff"/><text x="12" y="27" fill="#647c88" fontSize="12" clipPath={`url(#${labelClip})`}>LOT / 현재 공정</text>
  {ticks.map(({x},i)=><line key={i} x1={x} y1={38} x2={x} y2={height} stroke="#e8eff2"/>)}{axisLabels}
  {grouped.map((route,i)=>{const y=48+i*rowHeight,current=route.find(r=>r.status==='current'),unknown=route.filter(r=>r.status==='upcoming'&&!r.start_at).length;return <g key={`${route[0].lot_cd}/${route[0].lot_id}`}><line x1={0} y1={y+rowHeight-1} x2={width} y2={y+rowHeight-1} stroke="#edf2f4"/><g clipPath={`url(#${labelClip})`}><text x={12} y={y+17} fontSize={12} fill="#244651" fontWeight={600}>{route[0].lot_id}</text><text x={12} y={y+34} fontSize={10} fill="#69808a">{summaryFor(route[0])?.current_operation??current?.operation}{current?.overrun?' · 소요 초과':''}</text></g>
   <g className="gantt-ribbon" pointerEvents="none">{ribbons[i].filter(band=>band.end>start&&band.start<start+span).map((band,index)=>{const x1=Math.max(left,px(band.start)),x2=Math.min(right,px(band.end));return <rect key={index} x={x1} y={y+ribbonY} width={Math.max(0,x2-x1)} height={18} fill={colors[band.status]}/>})}</g>
   {intervals[i].filter(interval=>interval.row.displayed!==false&&interval.start!==null&&interval.start>=start&&interval.start<=start+span).map((interval,index,markers)=>{
    const x=px(interval.start!),previous=markers[index-1],next=markers[index+1];
    const hitLeft=Math.max(left,x-4,previous?(px(previous.start!)+x)/2:left),hitRight=Math.min(right,x+4,next?(px(next.start!)+x)/2:right);
    return <g className="gantt-milestone" key={interval.row.operation_seq} role="button" tabIndex={0} aria-label={describe(interval)} onClick={()=>choose(interval.row)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(interval.row)}}}>
     <title>{describe(interval)}</title><rect x={hitLeft} y={y+ribbonY-7} width={Math.max(0,hitRight-hitLeft)} height={32} fill="transparent"/>
     <line x1={x} x2={x} y1={y+ribbonY-4} y2={y+ribbonY+22} stroke={interval.status==='current'?'#086f70':interval.status==='completed'?'#718c99':'#68a399'} strokeWidth={1} pointerEvents="none"/>
    </g>;
   })}
   {layoutKeyFlags(intervals[i].filter(interval=>interval.row.key_operation&&interval.start!==null&&interval.start>=start&&interval.start<=start+span).map(interval=>({x:px(interval.start!),label:interval.row.operation,interval})),left,right).map((flag,index)=>{
    const top=y+6+flag.lane*19,clip=`${labelClip}-flag-${i}-${index}`;
    return <g className="gantt-key-flag" key={index} role="button" tabIndex={0} aria-label={`핵심 공정: ${flag.items.map(item=>item.label).join(', ')}`} onClick={()=>choose(flag.items[0].interval.row)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(flag.items[0].interval.row)}}}>
     <title>{flag.items.map(item=>describe(item.interval)).join('\n\n')}</title>
     <path d={`M${flag.x},${y+ribbonY} L${flag.x},${top+17} L${flag.labelX+7},${top+17}`} fill="none" stroke="#b18a43" strokeWidth={1} pointerEvents="none"/>
     <circle cx={flag.x} cy={y+ribbonY} r={2} fill="#ac7a24" pointerEvents="none"/>
     <rect x={flag.labelX} y={top} width={flag.width} height={17} rx={3} fill="#fff8e9" stroke="#dbc798"/>
     <clipPath id={clip}><rect x={flag.labelX+7} y={top} width={Math.max(0,flag.width-14)} height={17}/></clipPath>
     <text x={flag.labelX+7} y={top+12} fontSize={10} fontWeight={500} fill="#76541e" clipPath={`url(#${clip})`} pointerEvents="none">{flagLabel(flag.items[0].label,flag.items.length,flag.width)}</text>
    </g>;
   })}
   <g role="button" tabIndex={0} aria-label={`${route[0].lot_id} 전체 공정 상세`} onClick={()=>choose(current??route[0])} onKeyDown={e=>{if(e.key==='Enter')choose(current??route[0])}} style={{cursor:'pointer'}}><rect x={right+2} y={y+6} width={32} height={32} fill="transparent"/><text x={right+18} y={y+26} textAnchor="middle" fontSize={16} fill="#087f80">{unknown?'?':'›'}</text><title>{unknown?`예상 시점이 없는 공정 ${unknown}개 · 상세 보기`:'전체 공정 상세'}</title></g>
  </g>})}
  {base>=start&&base<=start+span&&<g><line x1={px(base)} y1={36} x2={px(base)} y2={height} stroke="#087f80" strokeWidth={1.5} strokeDasharray="3 3"/><circle cx={px(base)} cy={37} r={3} fill="#087f80"/></g>}
 </svg>{!grouped.length&&<div className="gantt-empty">조건에 맞는 진행 중 랏이 없습니다.</div>}</div>
 {selectedRoute&&selected&&<aside className="gantt-detail"><div><strong>{selected.lot_id}</strong><IconButton size="small" aria-label="공정 상세 닫기" onClick={()=>setSelected(null)}><X size={16}/></IconButton></div>{onFocusLot&&!result.meta.detail_lot&&<Button size="small" onClick={()=>focus(selected.lot_id)}>이 랏 공정 보기</Button>}{selected.remark&&<p>{selected.remark}</p>}<ol>{selectedRoute.slice(stepPage*50,(stepPage+1)*50).map(r=><li key={r.operation_seq} className={r.operation_seq===selected.operation_seq?'selected':''}><button onClick={()=>choose(r)}><b>{r.operation_seq}. {r.operation}</b><small>주요 공정 {names[r.status]} · {r.expected_hours===null?'TAT 없음':`${r.expected_hours.toLocaleString()}시간`}</small></button></li>)}</ol>{selectedRoute.length>50&&<div className="gantt-step-pages"><Button size="small" disabled={stepPage===0} onClick={()=>setStepPage(v=>v-1)}>이전</Button><small>{stepPage+1}/{Math.ceil(selectedRoute.length/50)}</small><Button size="small" disabled={(stepPage+1)*50>=selectedRoute.length} onClick={()=>setStepPage(v=>v+1)}>다음</Button></div>}<div className="gantt-detail-values"><b>{selected.operation}</b><span>예상 TAT {selected.expected_hours==null?'미정':`${hours(selected.expected_hours)}시간`}</span>{selectedInterval&&<><span>표시 구간 {intervalStamp(selectedInterval.start)} → {intervalStamp(selectedInterval.end)}</span><span>구간 시간 {hours(selectedInterval.hours)}시간{selectedInterval.extended?' · 중간 공정 포함':''}</span>{selectedInterval.next&&<span>다음 주요 공정 {selectedInterval.next.operation}</span>}{selectedInterval.toPlanEnd&&<span>전체 플랜 종료까지 포함</span>}</>}<span>주요 공정 시작 {stamp(selected.start_at)}</span><span>{selected.status==='completed'?'완료':selected.status==='current'?'조회 기준':'예상 종료'} {stamp(selected.end_at)}</span>{selected.current_expected_end&&<span>예상 완료 {stamp(selected.current_expected_end)}</span>}<span>{selected.estimated?'추정 구간 · 대기 포함':'시작 시각 기준'}</span><span>주요 공정 TAT {selected.expected_hours??'—'}시간 · 표본 {selected.tat_samples}건</span><span>{selected.tat_basis==='same_lot_cd'?'동일 lot_cd 기준':selected.tat_basis==='all_lot_cd'?'조회 범위 전체 lot_cd 기준':'TAT 표본 없음'}</span>{selected.issue&&<p>{selected.issue}</p>}</div></aside>}
 </div><div className="gantt-footer"><span>{result.meta.lot_count as number}개 랏 · {page+1}페이지</span>{onPage&&<><Button size="small" disabled={page===0} onClick={()=>onPage(page-1)}>이전</Button><Button size="small" disabled={!result.meta.has_more} onClick={()=>onPage(page+1)}>다음</Button></>}</div></div>
}

export default function LiveGantt({card,context,autoRefresh=false,onResult}:{card:CardSpec;context:QueryContext;autoRefresh?:boolean;onResult?:(result:ChartResult)=>void}){
 const [detailLot,setDetailLot]=useState('');
 const [search,setSearch]=useState(''),[settled,setSettled]=useState(''),[page,setPage]=useState(0);
 useEffect(()=>{const timer=setTimeout(()=>{setSettled(search);setPage(0)},350);return()=>clearTimeout(timer)},[search]);
 const queryContext=useMemo(()=>({...context,gantt_detail_lot:detailLot,gantt_page:page,gantt_search:settled,gantt_lot_cd:null}),[context,page,settled,detailLot]);
 const result=useQuery({...chartQueryOptions(card,queryContext,requestChart),placeholderData:previous=>previous,...(autoRefresh?dashboardRefreshOptions:{})});
 useEffect(()=>{if(result.data)onResult?.(result.data)},[result.data,onResult]);
 if(result.isPending)return <Loading/>;
 if(result.error&&(!result.data||(result.error instanceof ApiError&&result.error.status<500)))return <ErrorNotice error={result.error}/>;
 if(!result.data)return null;
 return <><GanttView result={result.data} page={page} onPage={setPage} search={search} onSearch={v=>{setDetailLot('');setSearch(v)}} onFocusLot={v=>{setPage(0);setDetailLot(v)}}/>{result.error&&<Alert severity="warning">최신 조회에 실패해 이전 결과를 표시합니다.</Alert>}<div className="chart-meta"><span className="meta-dot"/>{stamp(result.data.meta.queried_at)} 조회{result.data.meta.cached&&<span className="cache-tag">캐시</span>}</div></>;
}
