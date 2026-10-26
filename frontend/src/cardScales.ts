import type {CardSpec,Column,ChartResult} from './types';

export type Axis='x'|'y';
export type AxisScale='linear'|'log';
export const isNumeric=(column:Column)=>/^(smallint|integer|bigint|numeric|real|double precision|decimal|float|int)/i.test(column.type);

export function supportsLogAxis(card:CardSpec,axis:Axis,columns:Column[]):boolean {
 if(!['scatter','line','bar','box','histogram'].includes(card.kind))return false;
 if(axis==='x')return ['scatter','line','histogram'].includes(card.kind)&&columns.some(c=>c.name===card.x&&isNumeric(c));
 return card.kind==='histogram'||card.value_expression!=null||['count','yield'].includes(card.aggregate)||columns.some(c=>c.name===card.y&&isNumeric(c));
}

export function normalizeAxisScales(card:CardSpec,columns:Column[]):CardSpec {
 const options={...card.options};
 for(const axis of ['x','y'] as const)if(options[`${axis}_scale`]==='log'&&!supportsLogAxis(card,axis,columns)){
  options[`${axis}_scale`]='linear';
  options[`${axis}_min`]=undefined;
  options[`${axis}_max`]=undefined;
 }
 return {...card,options};
}

export function changeAxisScale(card:CardSpec,axis:Axis,scale:AxisScale):CardSpec {
 const options={...card.options,[`${axis}_scale`]:scale};
 // A linear zero baseline cannot be reused on a log axis; fall back to auto.
 if(scale==='log')for(const bound of ['min','max'] as const){
  const key=`${axis}_${bound}` as const,value=options[key];
  if(value!=null&&(!Number.isFinite(value)||value<=0))options[key]=undefined;
 }
 return {...card,options};
}

export function axisScaleIssue(card:CardSpec,columns:Column[]):string|null {
 for(const axis of ['x','y'] as const){
  if(card.options[`${axis}_scale`]!=='log')continue;
  if(!supportsLogAxis(card,axis,columns))return `${axis.toUpperCase()}축에는 로그 스케일을 사용할 수 없습니다.`;
  if(['min','max'].some(bound=>{const value=card.options[`${axis}_${bound}` as `${Axis}_min`|`${Axis}_max`];return value!=null&&(!Number.isFinite(value)||value<=0)}))return `${axis.toUpperCase()}축 로그 범위에는 0보다 큰 값을 입력하세요.`;
 }
 return null;
}

/** Plotly log ranges use exponents; the editor and stored card use real values. */
export function plotlyAxisScale(card:CardSpec,axis:Axis){
 const log=card.options[`${axis}_scale`]==='log';
 const min=card.options[`${axis}_min`],max=card.options[`${axis}_max`];
 const convert=(value:number|null|undefined)=>value==null?null:log?Math.log10(value):value;
 return {type:log?'log':'-',range:min!=null||max!=null?[convert(min),convert(max)]:undefined};
}

export function logScaleWarning(card:CardSpec,rows:ChartResult['rows']):string|null {
 const affected:string[]=[];
 for(const axis of ['x','y'] as const){
  if(card.options[`${axis}_scale`]!=='log')continue;
  const values=rows.map(row=>row[axis]).filter(value=>value!=null&&value!=='').map(Number).filter(Number.isFinite);
  if(values.length&&!values.some(value=>value>0))return `${axis.toUpperCase()}축에 0보다 큰 값이 없어 로그 차트를 표시할 수 없습니다.`;
  if(values.some(value=>value<=0))affected.push(axis.toUpperCase());
 }
 return affected.length?`${affected.join('·')}축의 0 이하 값은 로그 스케일에서 표시되지 않습니다.`:null;
}
