import {useEffect,useRef,useState} from 'react';
import type {CardSpec,ChartResult} from './types';
import {getFullPlotly} from './plotlyRuntime';
import {ErrorNotice} from './components';

export default function ScriptPlot({card,result}:{card:CardSpec;result:ChartResult}){
 const element=useRef<HTMLDivElement>(null),[error,setError]=useState<unknown>(null);
 useEffect(()=>{
  const target=element.current;if(!target||!result.figure)return;
  let alive=true,plotly:any;setError(null);target.dataset.exportReady='false';
  const observer=new ResizeObserver(()=>{if(alive&&plotly&&target.clientWidth&&target.clientHeight)plotly.relayout(target,{width:target.clientWidth,height:target.clientHeight}).catch(()=>{})});
  observer.observe(target);
  getFullPlotly().then(async p=>{
   if(!alive)return;plotly=p;
   const figure=structuredClone(result.figure!);
   await p.react(target,figure.data,{...figure.layout,autosize:true,width:target.clientWidth,height:target.clientHeight},
    {responsive:true,displaylogo:false,displayModeBar:false,scrollZoom:false,editable:false});
   if(alive)target.dataset.exportReady='true';
  }).catch(e=>{if(alive)setError(e)});
  return()=>{alive=false;observer.disconnect();if(plotly)plotly.purge(target)};
 },[result.figure,card.id]);
 return <><ErrorNotice error={error}/><div ref={element} className="plot-container script-plot" aria-label={`${card.title} 차트`}/></>;
}
