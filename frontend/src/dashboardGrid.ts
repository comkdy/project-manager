import type {CSSProperties} from 'react';

// Keep the existing 71px row pitch while giving the chart more of each cell.
export const GRID_ROW_HEIGHT=59;
export const GRID_GAP=12;
export const GRID_BREAKPOINTS={lg:1100,md:850,sm:600,xs:0};
export const GRID_COLUMNS={lg:12,md:12,sm:6,xs:6};
export const COMPACT_COLUMNS={lg:1,md:1,sm:1,xs:1};
export type GridMetrics={width:number;cols:number};

export function gridSteps({width,cols}:GridMetrics){
 return {x:(width+GRID_GAP)/cols,y:GRID_ROW_HEIGHT+GRID_GAP};
}

// GridItem tracks the pointer in pixels. Quantize its rendered box using the
// same geometry as its grid placeholder, so it snaps before the pointer is up.
export function snapGridStyle(style:CSSProperties,metrics:GridMetrics):CSSProperties{
 const pixel=(value:unknown)=>typeof value==='number'?value:typeof value==='string'&&/^-?\d+(\.\d+)?px$/.test(value)?parseFloat(value):NaN;
 const [width,height,left,top]=[style.width,style.height,style.left,style.top].map(pixel);
 if(metrics.width<=0||![width,height,left,top].every(Number.isFinite))return style;
 const step=gridSteps(metrics);
 const w=Math.max(1,Math.min(metrics.cols,Math.round((width+GRID_GAP)/step.x)));
 const h=Math.max(1,Math.round((height+GRID_GAP)/step.y));
 const x=Math.max(0,Math.min(metrics.cols-w,Math.round(left/step.x)));
 const y=Math.max(0,Math.round(top/step.y));
 return {...style,width:Math.round(w*step.x-GRID_GAP),height:h*step.y-GRID_GAP,left:Math.round(x*step.x),top:y*step.y};
}
