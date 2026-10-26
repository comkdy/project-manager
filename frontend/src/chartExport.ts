import {getPlotly,getFullPlotly} from './plotlyRuntime.ts';

type Format='png'|'svg';
const SVG_NS='http://www.w3.org/2000/svg';
const svgNode=(tag:string,attributes:Record<string,string|number>={})=>{
 const node=document.createElementNS(SVG_NS,tag);
 for(const [name,value] of Object.entries(attributes))node.setAttribute(name,String(value));
 return node;
};

/** Snapshot the live graph: no new data query, layout rebuild, or axis reset. */
export async function livePlotSvg(plot:HTMLElement,plotly:{toImage:(element:HTMLElement,options:object)=>Promise<string>}){
 if(plot.dataset.exportReady!=='true'||!plot.isConnected||!plot.clientWidth||!plot.clientHeight)
  throw new Error('차트가 표시된 후 다시 내보내 주세요.');
 const image=await plotly.toImage(plot,{format:'svg',width:plot.clientWidth,height:plot.clientHeight});
 const comma=image.indexOf(',');
 if(comma<0)throw new Error('차트 이미지를 만들지 못했습니다.');
 return image.slice(0,comma).includes(';base64')?new TextDecoder().decode(Uint8Array.from(atob(image.slice(comma+1)),c=>c.charCodeAt(0))):decodeURIComponent(image.slice(comma+1));
}

// KPI and card headings are HTML. Copy their displayed text positions/styles
// into ordinary SVG text, keeping exports portable (no foreignObject or HTML).
function copyText(source:HTMLElement,origin:DOMRect,target:Element){
 const walker=document.createTreeWalker(source,NodeFilter.SHOW_TEXT);
 while(walker.nextNode()){
  const text=walker.currentNode as Text,parent=text.parentElement!;
  const style=getComputedStyle(parent);
  if(style.visibility==='hidden'||style.display==='none')continue;
  const range=document.createRange();
  let line='',left=0,top=0;
  const flush=()=>{
   if(!line.trim())return;
   const node=svgNode('text',{x:left-origin.left,y:top-origin.top,'dominant-baseline':'text-before-edge',
    'font-family':style.fontFamily,'font-size':style.fontSize,'font-weight':style.fontWeight,
    'font-style':style.fontStyle,'letter-spacing':style.letterSpacing,fill:style.color,'xml:space':'preserve'});
   node.textContent=line;target.append(node);
  };
  for(let i=0;i<text.length;i++){
   range.setStart(text,i);range.setEnd(text,i+1);
   const box=range.getBoundingClientRect();
   if(!box.height)continue;
   if(line&&Math.abs(box.top-top)>1){flush();line=''}
   if(!line){left=box.left;top=box.top}
   line+=text.data[i];
  }
  flush();range.detach();
 }
}

export async function cardSvg(card:HTMLElement){
 await document.fonts.ready;
 const origin=card.getBoundingClientRect(),heading=card.querySelector<HTMLElement>('.chart-card-heading-text');
 const gantt=card.querySelector<SVGSVGElement>('.gantt-svg');
 if(gantt){
  const drawing=gantt.cloneNode(true) as SVGSVGElement;
  const width=gantt.viewBox.baseVal.width,height=gantt.viewBox.baseVal.height+70;
  const svg=svgNode('svg',{xmlns:SVG_NS,width,height,viewBox:`0 0 ${width} ${height}`});
  svg.append(svgNode('rect',{width,height,fill:'#fff'}));
  if(heading)copyText(heading,origin,svg);
  drawing.setAttribute('x','0');drawing.setAttribute('y','60');
  svg.append(drawing);
  return {source:new XMLSerializer().serializeToString(svg),width,height};
 }
 const plot=card.querySelector<HTMLElement>('.plot-container'),kpi=card.querySelector<HTMLElement>('.kpi-value');
 if(!plot&&!kpi)throw new Error('표시할 데이터가 있는 차트만 내보낼 수 있습니다.');
 const content=(plot??kpi)!,box=content.getBoundingClientRect();
 // Export every Trellis panel at its current size, including panels scrolled
 // out of view. Scrolling must not shift the drawing over the card heading.
 const scroll=plot?.closest<HTMLElement>('.trellis-scroll');
 const drawingTop=box.top-origin.top+(scroll?.scrollTop??0);
 const width=card.clientWidth,height=Math.ceil(drawingTop+content.clientHeight+12);
 const svg=svgNode('svg',{xmlns:SVG_NS,width,height,viewBox:`0 0 ${width} ${height}`});
 svg.append(svgNode('rect',{width,height,fill:getComputedStyle(card).backgroundColor||'#fff'}));
 if(heading)copyText(heading,origin,svg);
 if(plot){
  const source=await livePlotSvg(plot,await (plot.classList.contains('script-plot')?getFullPlotly():getPlotly()));
  const parsed=new DOMParser().parseFromString(source,'image/svg+xml');
  if(parsed.querySelector('parsererror'))throw new Error('차트 이미지를 읽지 못했습니다.');
  const drawing=parsed.documentElement;
  drawing.setAttribute('x',String(box.left-origin.left));drawing.setAttribute('y',String(drawingTop));
  svg.append(document.importNode(drawing,true));
 }else if(kpi){
  copyText(kpi,origin,svg);
  const bar=kpi.querySelector<HTMLElement>('.kpi-bar'),fill=bar?.querySelector<HTMLElement>('i');
  if(bar&&fill){
   const gradient=svgNode('linearGradient',{id:'kpi-fill'});
   // The displayed bar's CSS gradient supplies the same color stops.
   const colors=getComputedStyle(fill).backgroundImage.match(/rgba?\([^)]+\)|#[\da-f]+/gi)??[];
   colors.forEach((color,i)=>gradient.append(svgNode('stop',{offset:`${100*i/Math.max(1,colors.length-1)}%`,'stop-color':color})));
   const defs=svgNode('defs');defs.append(gradient);svg.append(defs);
   for(const element of [bar,fill]){
    const rect=element.getBoundingClientRect(),style=getComputedStyle(element);
    svg.append(svgNode('rect',{x:rect.left-origin.left,y:rect.top-origin.top,width:rect.width,height:rect.height,
     rx:parseFloat(style.borderRadius)||0,fill:element===fill&&colors.length?'url(#kpi-fill)':style.backgroundColor}));
   }
  }
 }
 return {source:new XMLSerializer().serializeToString(svg),width,height};
}

async function pngBlob(source:Blob,width:number,height:number):Promise<Blob>{
 const url=URL.createObjectURL(source);
 try{
  const image=new Image();image.src=url;await image.decode();
  const canvas=document.createElement('canvas');canvas.width=width*2;canvas.height=height*2;
  const context=canvas.getContext('2d');if(!context)throw new Error('이미지를 만들 수 없습니다.');
  context.drawImage(image,0,0,canvas.width,canvas.height);
  return await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('PNG 변환에 실패했습니다.')),'image/png'));
 }finally{URL.revokeObjectURL(url)}
}

export async function downloadCardImage(card:HTMLElement,title:string,format:Format){
 const snapshot=await cardSvg(card);
 const vector=new Blob([snapshot.source],{type:'image/svg+xml;charset=utf-8'});
 const blob=format==='svg'?vector:await pngBlob(vector,snapshot.width,snapshot.height);
 const url=URL.createObjectURL(blob);
 try{
  const link=document.createElement('a');link.href=url;
  link.download=`${title.replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_')}.${format}`;
  document.body.append(link);link.click();link.remove();
 }finally{setTimeout(()=>URL.revokeObjectURL(url),1000)}
}
