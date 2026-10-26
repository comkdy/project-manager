import type {CardSpec,ChartKind,Column,Dataset} from './types';
import {editableValueCard,expressionIssue,expressionName,supportsExpression} from './cardExpressions.ts';
import {isNumeric,axisScaleIssue,normalizeAxisScales} from './cardScales.ts';
export {isNumeric} from './cardScales.ts';

export const aggregateNames:Record<CardSpec['aggregate'],string>={none:'집계 안 함',count:'개수',avg:'평균',sum:'합계',min:'최솟값',max:'최댓값',yield:'수율 (%)'};
export const hasYield=(columns:Column[])=>['passed_dies','tested_dies'].every(name=>columns.some(c=>c.name===name));
export const hasWaferYield=(columns:Column[])=>['passed','status'].every(name=>columns.some(c=>c.name===name));
export const hasCellValue=(kind:ChartKind)=>kind==='wafer';
export const hasGroups=(kind:ChartKind)=>['bar','line','scatter','box'].includes(kind);
export const supportsTrellis=(kind:ChartKind)=>['bar','line','scatter','histogram','box','wafer'].includes(kind);
export const hasAggregate=(kind:ChartKind)=>['bar','line','kpi'].includes(kind);
export const needsY=(card:CardSpec)=>hasCellValue(card.kind)||(!card.value_expression&&(['scatter','box'].includes(card.kind)||(['bar','line','kpi'].includes(card.kind)&&!['count','yield'].includes(card.aggregate)&&!(card.kind==='kpi'&&card.aggregate==='none'))));
export const aggregateChoices=(kind:ChartKind,columns:Column[]):CardSpec['aggregate'][]=>
 (['count','avg','sum','min','max',...(['bar','line'].includes(kind)?['none']:[]),...(hasYield(columns)?['yield']:[])] as CardSpec['aggregate'][]);

/** Count and yield have explicit sources even though neither reads the Y field. */
export function valueSource(card:CardSpec,columns:Column[]=[]):{selection:string;label:string;detail:string} {
 if(card.value_expression!=null)return {selection:'expression',label:expressionName(card),detail:card.aggregate==='none'&&!hasCellValue(card.kind)?'각 행의 값으로 계산':`각 컬럼 ${aggregateNames[card.aggregate==='none'?'avg':card.aggregate]} 후 계산`};
 if(card.kind==='wafer'&&card.aggregate==='none'&&!card.color)return hasWaferYield(columns)?{selection:'wafer-yield',label:'passed · status',detail:'검사 수율 (%)'}:{selection:'rows',label:'전체 행',detail:'셀별 행 개수'};
 if(card.kind==='histogram')return {selection:'rows',label:'전체 행',detail:'구간별 행 개수'};
 if(card.aggregate==='count'||(card.aggregate==='none'&&((hasCellValue(card.kind)&&!card.color)||card.kind==='kpi')))return {selection:'rows',label:'전체 행',detail:'행 개수'};
 if(card.aggregate==='yield')return {selection:'yield',label:'passed_dies / tested_dies',detail:'수율 (%)'};
 const column=hasCellValue(card.kind)?card.color:card.y;
 return {selection:column?`column:${column}`:'',label:column||'데이터 선택',detail:hasCellValue(card.kind)&&card.aggregate==='none'?'평균':aggregateNames[card.aggregate]};
}

export function chooseValueSource(card:CardSpec,selection:string):CardSpec {
 const key=hasCellValue(card.kind)?'color':'y';
 if(key==='y'&&selection!==`column:${card.y}`)card={...card,options:{...card.options,y_label:undefined}};
 if(selection==='expression'){
  if(card.aggregate==='yield')return editableValueCard(card);
  return {...card,[key]:undefined,value_expression:card.value_expression??(card[key]?[{kind:'column',name:card[key]!}]:[]),aggregate:['scatter','box'].includes(card.kind)||(card.aggregate==='none'&&['bar','line'].includes(card.kind))?'none':['sum','avg','min','max'].includes(card.aggregate)?card.aggregate:'avg'};
 }
 card={...card};delete card.value_expression;delete card.value_expression_name;
 if(card.options.value_format==='percent')card.options={...card.options,value_format:'number'};
 if(selection==='wafer-yield'&&card.kind==='wafer')return {...card,color:undefined,aggregate:'none'};
 if(selection==='rows'||selection==='yield')return {...card,[key]:undefined,aggregate:selection==='rows'?'count':'yield'};
 const column=selection.startsWith('column:')?selection.slice(7):undefined;
 const aggregate=['scatter','box'].includes(card.kind)?'none':['avg','sum','min','max'].includes(card.aggregate)||(card.aggregate==='none'&&['bar','line'].includes(card.kind))?card.aggregate:'avg';
 return {...card,[key]:column,aggregate};
}

export function valueAggregates(card:CardSpec,columns:Column[]):CardSpec['aggregate'][] {
 if(['scatter','box'].includes(card.kind))return ['none'];
 if(card.value_expression!=null)return ['avg','sum','min','max',...(['bar','line'].includes(card.kind)?['none'] as const:[])];
 if(card.kind==='wafer'&&card.aggregate==='none'&&!card.color)return ['none'];
 if(valueSource(card,columns).selection==='rows')return ['count'];
 if(card.aggregate==='yield')return ['yield'];
 const column=columns.find(c=>c.name===(hasCellValue(card.kind)?card.color:card.y));
 return [...(column&&!isNumeric(column)?[]:['avg','sum']),'min','max',...(['bar','line'].includes(card.kind)?['none']:[])] as CardSpec['aggregate'][];
}

export function newCard(dataset?:Dataset):CardSpec {
 const columns=dataset?.columns??[],names=new Set(columns.map(c=>c.name));
 const x=['project_name','lot_code','equipment_id','metric','wafer_code'].find(name=>names.has(name))??columns.find(c=>!isNumeric(c)&&!/timestamp|date/i.test(c.type))?.name??columns[0]?.name;
 return {id:crypto.randomUUID(),title:'새 시각화 카드',dataset_id:dataset?.id??'',dataset_version:dataset?.version??1,kind:'bar',renderer:'plotly',x,aggregate:hasYield(columns)?'yield':'count',filters:[],options:{palette:'teal',bins:30}};
}

/** Clear fields the selected chart no longer uses; never invent a measure. */
export function changeKind(card:CardSpec,kind:ChartKind,columns:Column[]):CardSpec {
 const next={...card,kind,options:{...card.options,x_min:undefined,x_max:undefined,y_min:undefined,y_max:undefined}};
 if(!supportsExpression(kind)){delete next.value_expression;delete next.value_expression_name;next.options.value_format='number'}
 if(['scatter','box'].includes(kind)||(kind==='wafer'&&next.value_expression==null))next.aggregate='none';
 else if(kind==='histogram')next.aggregate='count';
 else if(!aggregateChoices(kind,columns).includes(next.aggregate))next.aggregate='count';
 if(next.value_expression!=null&&!['scatter','box'].includes(kind)&&['none','count','yield'].includes(next.aggregate))next.aggregate='avg';
 if(kind==='kpi')next.x=undefined;
 if(kind==='histogram'&&!columns.some(c=>c.name===next.x&&isNumeric(c)))next.x=undefined;
 if(!needsY(next))next.y=undefined;
 if(!hasGroups(kind))next.color=undefined;
 if(!supportsTrellis(kind))next.trellis=undefined;
 if(kind==='scatter'||kind==='box'){
  if(!columns.some(c=>c.name===next.y&&isNumeric(c)))next.y=undefined;
 }
 if(next.x!==card.x)next.options.x_label=undefined;
 if(next.y!==card.y)next.options.y_label=undefined;
 return normalizeAxisScales(next,columns);
}

export function changeDataset(card:CardSpec,dataset:Dataset):CardSpec {
 const names=new Set(dataset.columns.map(c=>c.name));
 const next={...card,dataset_id:dataset.id,dataset_version:dataset.version,
  period:card.period?{...card.period,source_id:null,column:null}:undefined,
  x:card.x&&names.has(card.x)?card.x:undefined,y:card.y&&names.has(card.y)?card.y:undefined,
  color:card.color&&names.has(card.color)?card.color:undefined,filters:card.filters.filter(f=>names.has(f.column)),
  trellis:card.trellis&&names.has(card.trellis)?card.trellis:undefined,
  aggregate:card.aggregate==='yield'&&!hasYield(dataset.columns)?'count' as const:card.aggregate,
  options:{...card.options,x_min:undefined,x_max:undefined,y_min:undefined,y_max:undefined}};
 if(!next.x&&card.kind!=='kpi')next.x=newCard(dataset).x;
 if(card.kind==='histogram'&&!dataset.columns.some(c=>c.name===next.x&&isNumeric(c)))next.x=undefined;
 if(next.x!==card.x)next.options.x_label=undefined;
 if(next.y!==card.y)next.options.y_label=undefined;
 return normalizeAxisScales(next,dataset.columns);
}

export function cardIssue(card:CardSpec,columns:Column[]):string|null {
 if(!card.dataset_id)return '데이터셋을 선택하세요.';
 if(card.kind==='gantt')return !card.gantt||!card.gantt.status_source_id||!card.gantt.tat_source_id||!card.gantt.history_source_id?'간트차트의 테이블을 선택하세요.':null;
 if(card.kind!=='kpi'&&!card.x)return '차트의 X축을 눌러 데이터를 선택하세요.';
 if(needsY(card)&&!card.y)return card.kind==='kpi'?'값 영역을 눌러 데이터를 선택하세요.':'차트의 Y축을 눌러 데이터를 선택하세요.';
 if(card.value_expression!=null){const problem=expressionIssue(card.value_expression,columns);if(problem)return problem}
 if(hasCellValue(card.kind)&&['avg','sum','min','max'].includes(card.aggregate)&&!card.color&&card.value_expression==null)return '셀 값 영역을 눌러 데이터를 선택하세요.';
 if(card.trellis&&!supportsTrellis(card.kind))return '이 차트에는 Trellis를 사용할 수 없습니다.';
 for(const name of [card.x,card.y,card.color,card.trellis,...card.filters.map(f=>f.column)]){
  if(name&&!columns.some(c=>c.name===name))return `현재 데이터셋에 ${name} 컬럼이 없습니다.`;
 }
 const numericNames=[...(card.kind==='histogram'?[card.x]:[]),...(['scatter','box'].includes(card.kind)||(['bar','line','kpi'].includes(card.kind)&&['avg','sum'].includes(card.aggregate))?[card.y]:[]),...(hasCellValue(card.kind)&&card.color?[card.color]:[])];
 if(numericNames.some(name=>name&&!columns.some(c=>c.name===name&&isNumeric(c))))return '값에는 숫자형 데이터를 선택하세요.';
 const scaleIssue=axisScaleIssue(card,columns);
 if(scaleIssue)return scaleIssue;
 for(const axis of ['x','y'] as const){
  const min=card.options[`${axis}_min`],max=card.options[`${axis}_max`];
  if(min!=null&&max!=null&&min>=max)return '축 최솟값은 최댓값보다 작아야 합니다.';
 }
 return null;
}

/** Visual formatting reuses queried rows; only data-affecting settings trigger a query. */
export function previewQueryCard(card:CardSpec):CardSpec {
 const {value_expression_name,...queryCard}=card;
 return {...queryCard,renderer:'plotly',title:'카드 미리보기',options:{bins:card.options.bins??30,sort:card.options.sort??'asc'}};
}
