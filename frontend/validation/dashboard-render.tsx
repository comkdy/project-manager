// Standalone Vite validation page. Synthetic results, no API/metadata writes.
import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Plot} from '../src/Charts';
import type {CardSpec,ChartResult} from '../src/types';
import '../src/styles.css';

const started=performance.now();
const meta={mode:'sample' as const,returned_rows:20000,queried_at:new Date().toISOString(),cached:false,ignored_filters:[]};
const common={dataset_id:'synthetic',dataset_version:1,renderer:'plotly' as const,aggregate:'none' as const,filters:[],options:{},x:'x',y:'y'};
const cases:{card:CardSpec;result:ChartResult}[]=[
 {card:{...common,id:'bar',title:'프로젝트별 집계 · 3개',kind:'bar',aggregate:'avg'},result:{rows:[1,2,3].map(i=>({x:`P${i}`,y:90+i})),meta}},
 {card:{...common,id:'line',title:'추이 · 60개',kind:'line'},result:{rows:Array.from({length:60},(_,i)=>({x:i,y:90+Math.sin(i)})),meta}},
 {card:{...common,id:'scatter',title:'산점도 · 표시 한도 20,000점',kind:'scatter',color:'project'},result:{rows:Array.from({length:20000},(_,i)=>({x:(i%500)/5,y:Math.sin(i*.07)*20+50,color:`P${i%3}`})),meta}},
 {card:{...common,id:'box',title:'분포 · 표시 한도 20,000행',kind:'box'},result:{rows:Array.from({length:20000},(_,i)=>({x:`EQ${i%6}`,y:90+Math.sin(i*.07)*10})),meta}},
 {card:{...common,id:'wafer',title:'Wafer · 709점',kind:'wafer'},result:{rows:Array.from({length:961},(_,i)=>({x:i%31-15,y:Math.floor(i/31)-15,value:i%2?100:0})).filter(r=>r.x*r.x+r.y*r.y<=225),meta}},
];
function App(){
 const root=useRef<HTMLDivElement>(null),[elapsed,setElapsed]=useState<number|null>(null);
 useEffect(()=>{
  let frame=0,lastMutation=performance.now(),done=false;
  const check=()=>{
   const plots=[...(root.current?.querySelectorAll('.plot-container.js-plotly-plot')??[])];
   const ready=plots.length===cases.length&&plots.every(plot=>plot.querySelector('.main-svg'));
   if(ready&&performance.now()-lastMutation>=150){
    done=true;observer.disconnect();setElapsed(Math.round(performance.now()-started));return;
   }
   frame=requestAnimationFrame(check);
  };
  const observer=new MutationObserver(()=>{lastMutation=performance.now()});
  observer.observe(root.current!,{childList:true,subtree:true,attributes:true});
  frame=requestAnimationFrame(check);
  return()=>{observer.disconnect();if(!done)cancelAnimationFrame(frame)};
 },[]);
 return <main style={{padding:24}}><h1>대시보드 렌더링 검증</h1><p role="status">{elapsed===null?'렌더링 중':`렌더링 완료: ${elapsed} ms · KPI 2개 + 차트 5개 · 산점도/분포 각 20,000행`}</p><p>합성 응답 데이터 · DB 조회 제외 · 실제 Plot 컴포넌트 · 마지막 화면 변경 후 150ms 포함</p>
 <div ref={root} style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:16}}>
  {[['전체 수율','91.18%'],['분석 Wafer','300']].map(([title,value])=><section className="chart-card" key={title} style={{padding:20,height:160}}><h3>{title}</h3><strong style={{fontSize:44}}>{value}</strong></section>)}
  {cases.map(({card,result})=><section className="chart-card" key={card.id} style={{padding:16,height:320}}><h3>{card.title}</h3><div style={{height:255}}><Plot card={card} result={result}/></div></section>)}
 </div></main>
}
createRoot(document.getElementById('root')!).render(<App/>);
