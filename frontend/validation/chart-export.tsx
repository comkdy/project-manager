// Real Plot/export components with synthetic rows. No API or saved-card writes.
import React,{useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Plot} from '../src/Charts';
import {downloadCardImage} from '../src/chartExport';
import type {CardSpec,ChartResult} from '../src/types';
import '../src/styles.css';

const card:CardSpec={id:'wafer-export',title:'Wafer 패널 내보내기',dataset_id:'synthetic',dataset_version:1,
 kind:'wafer',renderer:'plotly',aggregate:'avg',x:'die_x',y:'die_y',color:'수율',trellis:'wafer_id',filters:[],options:{trellis_columns:2}};
const result:ChartResult={rows:[1,2,3].flatMap(wafer=>Array.from({length:121},(_,i)=>({
 x:i%11-5,y:Math.floor(i/11)-5,value:wafer*25,trellis:wafer
})).filter(p=>p.x*p.x+p.y*p.y<=25)),meta:{mode:'aggregate',returned_rows:243,queried_at:new Date().toISOString(),cached:false,ignored_filters:[]}};
function App(){
 const ref=useRef<HTMLElement>(null),[status,setStatus]=useState('패널을 아래로 스크롤한 후 내보내세요.');
 const download=async(format:'png'|'svg')=>{try{await downloadCardImage(ref.current!,card.title,format);setStatus(`${format.toUpperCase()} 완료`)}catch(error){setStatus(String(error))}};
 return <main style={{padding:20}}><h1>차트 내보내기 검증</h1><p>합성 데이터 · 실제 차트와 내보내기 함수 · 저장 데이터 변경 없음</p>
 <button onClick={()=>download('svg')}>SVG 내보내기</button> <button onClick={()=>download('png')}>PNG 내보내기</button><p role="status">{status}</p>
 <section ref={ref} className="chart-card" style={{height:340,maxWidth:650}}>
  <div className="chart-card-heading"><div className="chart-card-heading-text"><span className="card-category">검증용 데이터</span><h3>{card.title}</h3></div></div>
  <div className="chart-body"><Plot card={card} result={result}/></div>
 </section></main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
