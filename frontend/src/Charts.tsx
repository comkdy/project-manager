import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Alert} from '@mui/material';
import {ApiError,requestChart} from './api';
import {chartQueryOptions,dashboardRefreshOptions} from './dashboardRefresh';
import {chartPeriodDescription} from './dashboardPeriod';
import type {CardSpec,ChartResult,QueryContext} from './types';
import {ErrorNotice,Loading} from './components';
import {axisLabel,columnLabel} from './cardLabels';
import {expressionName,expressionLabel,isPercent} from './cardExpressions';
import {plotlyAxisScale,logScaleWarning} from './cardScales';
import {trellisPanels,trellisColumns,trellisFigure} from './plotTrellis';
import './trellis.css';
import LiveGantt,{GanttView} from './GanttChart';
import {getPlotly} from './plotlyRuntime';
import ScriptPlot from './ScriptPlot';
export const palettes:Record<string,string[]>={teal:['#079997','#386b9e','#92b9bc','#e7b25d','#ad7a9b','#8d969f'],viridis:['#440154','#414487','#2a788e','#22a884','#7ad151','#fde725'],blues:['#174b70','#247a9d','#40a2bd','#80cad8'],warm:['#ba6b45','#e8a05c','#efcc88','#8c6584']};
function colorScale(palette:string){
 const scales:Record<string,string[]>={teal:['#edf7f5','#087f80'],viridis:palettes.viridis,blues:['#eff7fc',...palettes.blues.slice().reverse()],warm:['#fff5df','#e8a05c','#ba6b45','#8c6584']};
 const colors=scales[palette]??scales.teal;
 return colors.map((color,index)=>[index/(colors.length-1),color]);
}
export const metricLabel=(card:CardSpec):string=>card.value_expression!=null?expressionName(card):axisLabel(card,'y')||'값';
function StandardPlot({card,result,editing=false,onEdit,onEditExpression}:{card:CardSpec;result:ChartResult;editing?:boolean;onEdit?:(part:'x'|'y'|'value'|'style')=>void;onEditExpression?:()=>void}){
 const ref=useRef<HTMLDivElement>(null);
 const panels=card.trellis?trellisPanels(result.rows):[];
 const [panelColumns,setPanelColumns]=useState(2);
 const plotHeight=card.trellis?Math.ceil(panels.length/panelColumns)*190+45:undefined;
 useEffect(()=>{
  let alive=true;
  let onResize:(()=>void)|undefined;
  const element=ref.current!;
  const resize=new ResizeObserver(()=>onResize?.());
  resize.observe(element);
  if(card.trellis)setPanelColumns(trellisColumns(panels.length,element.clientWidth,card.options.trellis_columns));
 getPlotly().then(p=>{if(!alive)return;const colors=palettes[card.options.palette??'teal']??palettes.teal;const rows=result.rows;const numericColor=card.kind==='scatter'&&!!card.color&&rows.some(r=>typeof r.color==='number')&&rows.every(r=>r.color==null||typeof r.color==='number');const groupMap=new Map<string,typeof rows>();for(const row of rows){let group=numericColor?'':String(row.color??'');if(!groupMap.has(group)&&groupMap.size>=39)group='기타 범주';const members=groupMap.get(group)??[];members.push(row);groupMap.set(group,members)}const groups=[...groupMap.keys()];let data:any[]=[];
 if(card.kind==='wafer'){
  const colorbar={thickness:9,len:0.7,title:{text:card.value_expression!=null?expressionName(card):result.meta.value_label??card.color??'값',side:'right',font:{size:10}}};
   data=[{type:'scatter',mode:'markers',x:rows.map(r=>r.x),y:rows.map(r=>r.y),customdata:rows.map(r=>r.trellis??null),showlegend:false,marker:{symbol:'square',size:10,color:rows.map(r=>r.value),colorscale:colorScale(card.options.palette??'teal'),showscale:true,colorbar},hovertemplate:'X %{x} · Y %{y}<br>%{marker.color}<extra></extra>'}];
 }else{data=groups.map((group,i)=>{const selected=groupMap.get(group)!;return {name:group||(card.value_expression!=null?expressionName(card):columnLabel(card,'y')||card.title),x:selected.map(r=>r.x),y:selected.map(r=>r.y),customdata:selected.map(r=>r.trellis??null),type:card.kind==='box'?'box':card.kind==='histogram'||card.kind==='bar'?'bar':'scatter',mode:card.kind==='line'?'lines+markers':'markers',marker:{color:numericColor?selected.map(r=>r.color):colors[i%colors.length],...(numericColor?{colorscale:colorScale(card.options.palette??'teal'),showscale:true,colorbar:{thickness:9,len:0.7,title:{text:card.color,font:{size:9}}}}:{}),size:card.kind==='scatter'?6:5,opacity:card.kind==='scatter'?.7:1,line:{width:0}},line:{width:2.5,shape:'linear'},boxpoints:false,hovertemplate:card.kind==='scatter'?'%{x}<br>%{y:.4f}<extra>%{fullData.name}</extra>':undefined}})}
 const compact=!card.trellis&&element.clientHeight<155;
 const axis={gridcolor:'#edf1f4',zeroline:false,linecolor:'#dfe7ec',tickfont:{size:compact?9:10,color:'#7c8e9b'},automargin:true};
 const margins=(short:boolean)=>({t:6,r:card.kind==='wafer'?38:8,b:short?21:editing?25:38,l:short?36:editing?36:46});
 const xTitle=editing?'':axisLabel(card,'x');
 const yTitle=editing?'':axisLabel(card,'y');
 const baseLayout:any={autosize:true,paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{family:'Segoe UI, Malgun Gothic, sans-serif',color:'#596e7d',size:11},margin:margins(compact),colorway:colors,showlegend:card.kind!=='wafer'&&groups.length>1&&!compact,legend:{orientation:'v',x:1.02,xanchor:'left',y:1,yanchor:'top',font:{size:10},tracegroupgap:2,maxheight:1},barmode:'group',bargap:.4,hovermode:'closest',
  xaxis:{...axis,...plotlyAxisScale(card,'x'),nticks:compact?4:undefined,tickfont:{...axis.tickfont,size:card.options.x_font_size??axis.tickfont.size},title:{text:compact?'':xTitle,font:{size:card.options.x_font_size??10},standoff:8}},
  yaxis:{...axis,...plotlyAxisScale(card,'y'),nticks:compact?3:undefined,tickfont:{...axis.tickfont,size:card.options.y_font_size??axis.tickfont.size},title:{text:compact?'':yTitle,font:{size:card.options.y_font_size??10},standoff:5},...(card.kind==='wafer'?{scaleanchor:'x',scaleratio:1}:{})},dragmode:'zoom'};
 const figure=card.trellis?trellisFigure(data,{...baseLayout,width:element.clientWidth,height:plotHeight},card,panels,panelColumns):{data,layout:baseLayout};
 element.dataset.exportReady='false';
 p.react(element,figure.data,figure.layout,
  {responsive:true,displaylogo:false,displayModeBar:editing?false:undefined,modeBarButtonsToRemove:['lasso2d','toImage'],toImageButtonOptions:{filename:card.title,format:'png',width:undefined,height:undefined,scale:2}}).then(()=>{if(alive)element.dataset.exportReady='true'});
 let lastWidth=element.clientWidth,lastHeight=element.clientHeight;
 onResize=()=>{
   if(!alive||!element.clientWidth||!element.clientHeight)return;
   if(element.clientWidth===lastWidth&&element.clientHeight===lastHeight)return;
   lastWidth=element.clientWidth;lastHeight=element.clientHeight;
   if(card.trellis){setPanelColumns(trellisColumns(panels.length,lastWidth,card.options.trellis_columns));p.relayout(element,{width:lastWidth,height:lastHeight}).catch(()=>{});return}
   const short=lastHeight<155;
   p.relayout(element,{width:lastWidth,height:lastHeight,margin:margins(short),
     showlegend:card.kind!=='wafer'&&groups.length>1&&!short,
     'xaxis.title.text':short?'':xTitle,'yaxis.title.text':short?'':yTitle,
     'xaxis.nticks':short?4:0,'yaxis.nticks':short?3:0,
     'xaxis.tickfont.size':card.options.x_font_size??(short?9:10),'yaxis.tickfont.size':card.options.y_font_size??(short?9:10)
   }).catch(()=>{});
 };
 });return()=>{alive=false;element.dataset.exportReady='false';resize.disconnect();getPlotly().then(p=>p.purge(element))};},[card,result,editing,panelColumns]);
 const plot=<div className={`plot-container ${onEditExpression?'plot-expression-editable':''}`} ref={ref} style={plotHeight?{height:plotHeight}:undefined} onClickCapture={event=>{
  if(!editing){if(onEditExpression&&(event.target as Element).closest(card.kind==='wafer'?'.colorbar':'.ytitle')){event.stopPropagation();onEditExpression()}return;}
  const target=event.target as Element;
  const axisClass=target.closest('[class$="tick"],[class$="title"]')?.getAttribute('class')??'';
  const part=/^x\d*(tick|title)$/.test(axisClass)||target.closest('.ewdrag')?'x':/^y\d*(tick|title)$/.test(axisClass)||target.closest('.nsdrag')?'y':target.closest('.colorbar')&&(card.kind==='wafer')?'value':target.closest('.legend,.colorbar,.point,.box,.js-line')?'style':null;
  if(part){event.stopPropagation();onEdit?.(part)}
 }}/>;
 return card.trellis?<div className="trellis-scroll" aria-label={`${card.trellis}별 Trellis`}>{plot}</div>:plot;
}
function StandardChart({card,context,autoRefresh=false,onResult,onEditExpression}:{card:CardSpec;context:QueryContext;autoRefresh?:boolean;onResult?:(r:ChartResult)=>void;onEditExpression?:()=>void}){
 const result=useQuery({...chartQueryOptions(card,context,requestChart),...(autoRefresh?dashboardRefreshOptions:{})});
 useEffect(()=>{if(result.data)onResult?.(result.data)},[result.data,onResult]);
 if(result.isPending)return <Loading/>;
 if(result.error&&(!result.data||(result.error instanceof ApiError&&result.error.status>=400&&result.error.status<500)))return <div className="chart-error"><ErrorNotice error={result.error}/><p>카드 설정과 데이터셋 컬럼을 확인하세요.</p></div>;
 if(!result.data?.rows.length&&!result.data?.figure)return <div className="no-results">{result.error&&<ErrorNotice error={result.error}/>}<span>조건에 맞는 데이터가 없습니다.</span><small>{chartPeriodDescription(result.data)}</small><small>카드 설정에서 기간 기준과 필터를 확인하세요.</small></div>;
 const data=result.data;
 const scaleWarning=logScaleWarning(card,data.rows);
 const hasExpression=card.value_expression!=null||card.aggregate==='yield';
 const editExpression=hasExpression?onEditExpression:undefined;
 const expressionButton=editExpression?<button type="button" className="chart-expression-name" aria-label={`${metricLabel(card)} 계산식 편집`} title={card.value_expression?expressionLabel(card.value_expression):'passed_dies ÷ tested_dies × 100'} onClick={event=>{event.stopPropagation();editExpression()}}>{metricLabel(card)}<span aria-hidden="true">↗</span></button>:null;
 const rawValue=data.rows[0]?.value??data.rows[0]?.y;const value=rawValue==null?null:Number(rawValue);
 return <><div className="chart-body">{card.kind==='kpi'&&!data.figure?<div className="kpi-value"><span>{value==null?'—':value.toLocaleString('ko-KR',{maximumFractionDigits:2})}{value!=null&&isPercent(card)&&<small>%</small>}</span><p>{expressionButton??metricLabel(card)} · {data.meta.mode==='aggregate'?(data.meta.period?'조회 기간 집계':'전체 데이터 집계'):'조회 결과'}</p><div className="kpi-bar"><i style={{width:`${isPercent(card)?Math.min(value??0,100):76}%`}}/></div></div>:<Plot card={card} result={data} onEditExpression={editExpression}/>}</div><div className="chart-meta">{card.kind!=='kpi'&&expressionButton}<span className="meta-dot"/><span>{data.meta.mode==='sample'?(data.meta.sample_method==='primary_key_prefix'?'앞쪽 행 표본':'표본'):data.meta.mode==='aggregate'?'집계':'전체'} {data.meta.returned_rows.toLocaleString()}행</span><span>·</span><span>{new Date(data.meta.queried_at).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})}</span>{data.meta.cached&&<span className="cache-tag">캐시</span>}</div>{result.error&&<Alert severity="warning" className="ignored-filter">최신 조회에 실패해 이전 결과를 표시합니다. 1분 후 다시 시도합니다.</Alert>}{scaleWarning&&<Alert severity="info" className="ignored-filter">{scaleWarning}</Alert>}{data.meta.truncated===true&&<Alert className="ignored-filter" severity="warning">표시 한도로 일부 결과가 생략되었습니다. 필터를 좁혀주세요.</Alert>}{data.meta.ignored_filters?.length>0&&<Alert className="ignored-filter" severity="info">미적용 필터: {data.meta.ignored_filters.join(', ')}</Alert>}</>
}

export function Chart(props:Parameters<typeof StandardChart>[0]){return props.card.kind==='gantt'?<LiveGantt card={props.card} context={props.context} autoRefresh={props.autoRefresh} onResult={props.onResult}/>:<StandardChart {...props}/>;}

export function Plot(props:Parameters<typeof StandardPlot>[0]){return props.result.figure?<ScriptPlot card={props.card} result={props.result}/>:props.card.kind==='gantt'?<GanttView result={props.result}/>:<StandardPlot {...props}/>;}
