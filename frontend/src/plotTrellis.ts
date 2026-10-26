import type {CardSpec,ChartResult} from './types';

export const panelKey=(value:unknown)=>JSON.stringify(value??null);
export function trellisPanels(rows:ChartResult['rows']):unknown[]{
 return [...new Map(rows.map(row=>[panelKey(row.trellis),row.trellis??null])).values()]
  .sort((a,b)=>a==null?1:b==null?-1:String(a).localeCompare(String(b),'ko',{numeric:true}));
}
export function trellisColumns(count:number,width:number,requested=0){
 return Math.max(1,Math.min(count,requested||Math.min(4,Math.max(1,Math.floor(width/240)))));
}
const escapeText=(value:unknown)=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

/** Split aligned trace arrays, keeping color identities and shared axis ranges. */
export function trellisFigure(data:any[],layout:any,card:CardSpec,panels:unknown[],columns:number){
 const rows=Math.ceil(panels.length/columns),xGap=columns>1?.10:0,yGap=rows>1?Math.min(.20,45/(layout.height-70)):0;
 const width=(1-xGap*(columns-1))/columns,height=(1-yGap*(rows-1))/rows;
 const figure={data:[] as any[],layout:{...layout,annotations:[] as any[],margin:{...layout.margin,t:28}}};
 // Infer from all panels together so a panel with numeric-looking text cannot
 // choose a different axis type and silently stop sharing its scale.
 const sharedAxis=(axis:'x'|'y')=>{
  const base=layout[`${axis}axis`],values=data.flatMap(trace=>trace[axis]).filter(value=>value!=null);
  const type=base.type&&base.type!=='-'?base.type:values.every(value=>typeof value==='number')?'linear':values.every(value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}(T|$)/.test(value))?'date':'category';
  return {...base,type,...(type==='category'?{categoryorder:'array',categoryarray:[...new Set(values)]}:{})};
 };
 const sharedX=sharedAxis('x'),sharedY=sharedAxis('y');
 const wafer=card.kind==='wafer';
 if(wafer){
  for(const [axis,shared] of [['x',sharedX],['y',sharedY]] as const){
   const values=data.flatMap(trace=>trace[axis]).filter(value=>typeof value==='number'&&Number.isFinite(value));
   shared.range=[shared.range?.[0]??Math.min(...values)-.5,shared.range?.[1]??Math.max(...values)+.5];
   shared.autorange=false;shared.constrain='domain';
  }
 }
 const seen=new Set<number>();
 panels.forEach((panel,index)=>{
  const suffix=index?String(index+1):'',xName=`x${suffix}`,yName=`y${suffix}`;
  const col=index%columns,row=Math.floor(index/columns);
  const xDomain=[col*(width+xGap),col*(width+xGap)+width],top=1-row*(height+yGap),yDomain=[top-height,top];
  figure.layout[`xaxis${suffix}`]={...sharedX,domain:xDomain,anchor:yName,...(index&&!wafer?{matches:'x'}:{}),nticks:4,title:{...layout.xaxis.title,text:row===rows-1?layout.xaxis.title.text:''}};
  figure.layout[`yaxis${suffix}`]={...sharedY,domain:yDomain,anchor:xName,...(wafer?{scaleanchor:xName,scaleratio:1}:index?{matches:'y'}:{}),nticks:4,title:{...layout.yaxis.title,text:col===0?layout.yaxis.title.text:''}};
  const label=panel==null?'(빈 값)':panel===''?'(빈 문자열)':String(panel);
  figure.layout.annotations.push({text:escapeText(label),hovertext:escapeText(`${card.trellis}: ${label}`),x:(xDomain[0]+xDomain[1])/2,y:top,xref:'paper',yref:'paper',yanchor:'bottom',yshift:5,showarrow:false,font:{size:11,color:'#315667'}});
  data.forEach((trace,traceIndex)=>{
   const indices=(trace.customdata as unknown[]).flatMap((value,i)=>panelKey(value)===panelKey(panel)?[i]:[]);
   if(!indices.length)return;
   const numericColor=Array.isArray(trace.marker?.color);
   const finiteColors=numericColor?trace.marker.color.filter((v:unknown)=>typeof v==='number'&&Number.isFinite(v)):[];
   figure.data.push({...trace,x:indices.map(i=>trace.x[i]),y:indices.map(i=>trace.y[i]),customdata:indices.map(i=>trace.customdata[i]),xaxis:xName,yaxis:yName,
    legendgroup:String(traceIndex),showlegend:!wafer&&!seen.has(traceIndex),
    marker:{...trace.marker,...(wafer?{size:Math.max(2,Math.min(10,
     ((layout.width??600)-(layout.margin.l??0)-(layout.margin.r??0))*width/(sharedX.range[1]-sharedX.range[0]),
     (layout.height-28-(layout.margin.b??0))*height/(sharedY.range[1]-sharedY.range[0]))*.9)}:{}),
     ...(numericColor?{color:indices.map(i=>trace.marker.color[i]),showscale:!seen.has(traceIndex),...(finiteColors.length?{cmin:Math.min(...finiteColors),cmax:Math.max(...finiteColors)}:{})}:{})}});
   seen.add(traceIndex);
  });
 });
 return figure;
}
