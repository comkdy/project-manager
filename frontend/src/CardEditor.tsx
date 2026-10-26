import {DatasetLabel} from './DatasetScopeBadge';
import {useCallback,useEffect,useMemo,useRef,useState,type Ref} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,IconButton,MenuItem,Popover,Slider,Tab,Tabs,TextField,type PopoverActions} from '@mui/material';
import {BarChart3,ScatterChart,ChartLine,Grid3X3,Hash,Columns3,SlidersHorizontal,X,Save,Pencil,Palette,ChevronDown,MousePointer2,RefreshCw,Check} from 'lucide-react';
import {useQuery} from '@tanstack/react-query';
import {api,ApiError,post} from './api';
import type {CardSpec,ChartKind,ChartResult,Column,Dataset,QueryContext} from './types';
import {Plot,palettes} from './Charts';
import {ErrorNotice,FilterBuilder,Loading} from './components';
import {aggregateNames,cardIssue,changeDataset,changeKind,chooseValueSource,hasCellValue,hasGroups,hasWaferYield,isNumeric,needsY,previewQueryCard,supportsTrellis,valueAggregates,valueSource} from './cardEditing';
import {editableValueCard,expressionIssue,isPercent} from './cardExpressions';
import {axisLabel} from './cardLabels';
import {supportsLogAxis,normalizeAxisScales,changeAxisScale,axisScaleIssue,logScaleWarning,type AxisScale} from './cardScales';
import CardExpressionInput,{type ExpressionInputHandle} from './CardExpressionInput';
import './card-editor.css';
import {CardPeriodControls} from './PeriodControls';
import GanttEditor from './GanttEditor';
import PythonCardEditor from './PythonCardEditor';
import {blankPythonCard,type PythonConversion} from './pythonCards';
import {useLocalDraft,LocalDraftNotice,DraftConflictDialog} from './localDraft';
import {validCardDraft,cardDraftComparison,type CardEditDraft} from './editorDraftValues';
import type {CardDraftEditorProps,LatestCardDraft} from './cardDraftEditor';
export {newCard} from './cardEditing';

const kinds:{value:ChartKind;name:string;icon:typeof BarChart3}[]=[{value:'bar',name:'막대',icon:BarChart3},{value:'line',name:'선',icon:ChartLine},{value:'scatter',name:'산점도',icon:ScatterChart},{value:'histogram',name:'히스토그램',icon:BarChart3},{value:'box',name:'상자',icon:Columns3},{value:'wafer',name:'Wafer map',icon:Grid3X3},{value:'kpi',name:'KPI',icon:Hash},{value:'gantt',name:'간트차트',icon:ChartLine}];
type Part='title'|'x'|'y'|'value'|'style';
const partNames:Record<Part,string>={title:'카드 제목',x:'X축',y:'Y축',value:'셀 값',style:'색상과 스타일'};

function DataField({label,value,columns,onChange,optional=false}:{label:string;value?:string|null;columns:Column[];onChange:(value:string|undefined)=>void;optional?:boolean}){
 return <TextField select fullWidth label={label} value={value??''} onChange={e=>onChange(e.target.value||undefined)}>
  <MenuItem value="">{optional?'사용 안 함':'데이터 선택'}</MenuItem>
  {value&&!columns.some(c=>c.name===value)&&<MenuItem value={value} disabled>{value} · 확인 필요</MenuItem>}
  {columns.map(c=><MenuItem key={c.name} value={c.name}>{c.name}<span className="select-type">{c.type}</span></MenuItem>)}
 </TextField>;
}

function ValueDataField({card,columns,onChange,expressionDraft,onExpressionDraft,expressionInput}:{expressionInput:Ref<ExpressionInputHandle>;card:CardSpec;columns:Column[];onChange:(card:CardSpec)=>void;expressionDraft:string;onExpressionDraft:(value:string)=>void}){
 const source=valueSource(card,columns),key=hasCellValue(card.kind)?'color':'y';
 const choices=columns.filter(c=>isNumeric(c)||c.name===card[key]);
 return <><TextField select fullWidth label={hasCellValue(card.kind)?'셀에 표시할 데이터':'표시할 데이터'} value={source.selection} onChange={e=>{onExpressionDraft('');onChange(chooseValueSource(card,e.target.value))}}>
  <MenuItem value="">데이터 선택</MenuItem>
  {card.kind==='wafer'&&hasWaferYield(columns)&&<MenuItem value="wafer-yield"><span>passed · status</span><span className="select-type">검사 수율</span></MenuItem>}
  {choices.map(c=><MenuItem key={c.name} value={`column:${c.name}`}>{c.name}<span className="select-type">{c.type}</span></MenuItem>)}
  <MenuItem value="expression">직접 계산식<span className="select-type">Custom expression</span></MenuItem>
  {!['scatter','box'].includes(card.kind)&&<MenuItem value="rows">전체 행<span className="select-type">행 개수</span></MenuItem>}
 </TextField>
 {card.value_expression!=null&&<><TextField fullWidth label="계산식 이름" placeholder="예: 수율" value={card.value_expression_name??''} inputProps={{maxLength:80}} onChange={e=>onChange({...card,value_expression_name:e.target.value})}/><CardExpressionInput commitRef={expressionInput} tokens={card.value_expression} columns={columns} draft={expressionDraft} onDraft={onExpressionDraft} onChange={value_expression=>onChange({...card,value_expression})}/></>}
 </>;
}

function StandardCardEditor({initial,datasets,context,onClose,onSave,focusValue=false,onGantt,onPython,draftNotice,onEdited,onDraftChange,onBusyChange,recoveredExpression=''}:{initial:CardSpec;datasets:Dataset[];context:QueryContext;onClose:()=>void;onSave:(c:CardSpec)=>Promise<void>;focusValue?:boolean;onGantt:(card:CardSpec)=>void;onPython:(card:CardSpec)=>void}&CardDraftEditorProps){
 const [draft,setDraft]=useState<CardSpec>(()=>editableValueCard({...structuredClone(initial),renderer:'plotly'}));
 const [expressionDraft,setExpressionDraft]=useState(recoveredExpression);
 const expressionInput=useRef<ExpressionInputHandle>(null);
 const [error,setError]=useState<unknown>(null),[saving,setSaving]=useState(false);
 useEffect(()=>{onBusyChange?.(saving)},[saving,onBusyChange]);
 const [popup,setPopup]=useState<{part:Part;anchor:HTMLElement;card:CardSpec}|null>(null);
 const popoverAction=useRef<PopoverActions>(null);
 const [popoverPaper,setPopoverPaper]=useState<HTMLDivElement|null>(null);
 useEffect(()=>{
  if(!popoverPaper)return;
  let frame=0;
  const reposition=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>popoverAction.current?.updatePosition())};
  const observer=new ResizeObserver(reposition);
  observer.observe(popoverPaper);
  window.visualViewport?.addEventListener('resize',reposition);
  return()=>{observer.disconnect();cancelAnimationFrame(frame);window.visualViewport?.removeEventListener('resize',reposition)};
 },[popoverPaper]);
 const xButton=useRef<HTMLButtonElement>(null),yButton=useRef<HTMLButtonElement>(null),styleButton=useRef<HTMLButtonElement>(null),valueButton=useRef<HTMLButtonElement>(null);
 const liveCard=popup?.card??draft;
 useEffect(()=>{onDraftChange?.({card:liveCard,expressionDraft})},[liveCard,expressionDraft,onDraftChange]);
 const projectId=context.project_id??null;
 const latest=useQuery({queryKey:['versions',projectId,draft.dataset_id,'latest'],queryFn:()=>api<Dataset>(`/datasets/${encodeURIComponent(draft.dataset_id)}/latest${projectId!==null?`?project_id=${projectId}`:''}`),enabled:!!draft.dataset_id});
 const dataset=latest.data;
 useEffect(()=>{if(dataset&&draft.dataset_version!==dataset.version){setDraft(c=>({...c,dataset_version:dataset.version}));setPopup(p=>p?{...p,card:{...p.card,dataset_version:dataset.version}}:p)}},[dataset,draft.dataset_version]);
 const columns=dataset?.columns??[],numeric=columns.filter(isNumeric),issue=cardIssue(liveCard,columns);
 const patch=(value:Partial<CardSpec>)=>{if(saving)return;onEdited?.();setDraft(c=>({...c,...value}))};
 const queryJSON=JSON.stringify(previewQueryCard(liveCard));
 const [settledQuery,setSettledQuery]=useState(queryJSON);
 useEffect(()=>{const timer=setTimeout(()=>setSettledQuery(queryJSON),350);return()=>clearTimeout(timer)},[queryJSON]);
 const queryCard=useMemo(()=>JSON.parse(settledQuery) as CardSpec,[settledQuery]);
 const preview=useQuery({queryKey:['card-editor-preview',queryCard,context],queryFn:({signal})=>api<ChartResult>('/cards/query',{method:'POST',body:JSON.stringify({card:queryCard,context}),signal}),enabled:!!dataset&&!issue&&queryJSON===settledQuery});
 const current=queryJSON===settledQuery,ready=!!dataset&&!issue&&current&&preview.isSuccess&&!preview.isFetching;
 const result=!!dataset&&!issue&&current?preview.data:undefined,value=result?.rows[0]?.value;
 const showAxes=draft.kind!=='kpi',part=popup?.part;
 const source=valueSource(liveCard,columns),measureAxis=['bar','line','kpi','scatter','box'].includes(draft.kind),cellValue=hasCellValue(draft.kind);
 const yLabel=axisLabel(liveCard,'y')||'데이터 선택';
 const settings=liveCard;
 const setSettings=(card:CardSpec)=>{if(saving)return;onEdited?.();setPopup(p=>p?{...p,card:normalizeAxisScales(card,columns)}:p)};
 const settingsPatch=(value:Partial<CardSpec>)=>{if(saving)return;onEdited?.();setPopup(p=>p?{...p,card:normalizeAxisScales({...p.card,...value},columns)}:p)};
 const settingsOption=(value:Partial<CardSpec['options']>)=>{if(saving)return;onEdited?.();setPopup(p=>p?{...p,card:{...p.card,options:{...p.card.options,...value}}}:p)};
 const editingValue=part==='value'||(part==='y'&&measureAxis);
 const scaleIssue=axisScaleIssue(settings,columns);
 const popupIssue=scaleIssue??(editingValue&&settings.value_expression!=null?(expressionDraft.trim()?null:expressionIssue(settings.value_expression,columns)):part==='title'&&!settings.title.trim()?'제목을 입력하세요.':null);
 const closePopup=()=>{setPopup(null);setExpressionDraft('')};
 const open=(next:Part,anchor:HTMLElement)=>{if(saving)return;setExpressionDraft('');setPopup({part:next,anchor,card:structuredClone(draft)})};
 const applyPopup=()=>{
  if(!popup||popupIssue)return;
  let card=popup.card;
  if(editingValue&&card.value_expression!=null){const tokens=expressionInput.current?.complete();if(!tokens)return;card={...card,value_expression:tokens}}
  setDraft(card);closePopup();
 };
 const openPlotPart=(next:'x'|'y'|'value'|'style')=>{const anchor=(next==='x'?xButton:next==='y'?yButton:next==='value'?valueButton:styleButton).current;if(anchor)open(next,anchor)};
 const setAggregate=(aggregate:CardSpec['aggregate'])=>{
  const next={...settings,aggregate};
  if(!needsY(next))next.y=undefined;
  if(hasCellValue(next.kind)&&['count','yield'].includes(aggregate))next.color=undefined;
  setSettings(next);
 };
 const aggregateField=<TextField select fullWidth label={settings.value_expression!=null?'각 컬럼의 집계 방식':'계산 방식'} value={settings.aggregate==='none'&&cellValue&&(settings.color||settings.value_expression)?'avg':settings.aggregate} onChange={e=>setAggregate(e.target.value as CardSpec['aggregate'])}>{valueAggregates(settings,columns).map(value=><MenuItem key={value} value={value}>{aggregateNames[value]}</MenuItem>)}</TextField>;
 const formatField=settings.value_expression!=null?<TextField select fullWidth label="값 표시" value={settings.options.value_format??'number'} onChange={e=>settingsOption({value_format:e.target.value as 'number'|'percent'})}><MenuItem value="number">숫자</MenuItem><MenuItem value="percent">백분율 기호 (%)</MenuItem></TextField>:null;
 const valueField=<ValueDataField expressionInput={expressionInput} card={settings} columns={columns} onChange={setSettings} expressionDraft={expressionDraft} onExpressionDraft={value=>{onEdited?.();setExpressionDraft(value)}}/>;
 const calculationDetails=valueAggregates(settings,columns).length>1||formatField?<details className="visual-axis-details"><summary>세부 설정<ChevronDown size={15}/></summary><div><div className="visual-field-row">{valueAggregates(settings,columns).length>1&&aggregateField}{formatField}</div></div></details>:null;
 async function save(){
  setError(null);setSaving(true);
  try{await post('/cards/query',{card:draft,context});await onSave(draft);onClose()}catch(e){setError(e)}finally{setSaving(false)}
 }
 async function openPython(){
  setError(null);setSaving(true);
  try{onPython(issue?blankPythonCard(draft):(await post<PythonConversion>('/cards/python-script',{card:draft,context})).card)}
  catch(e){setError(e)}finally{setSaving(false)}
 }
 const axisPopup=(axis:'x'|'y')=>{
  const showsMeasure=axis==='y'&&measureAxis;
  const autoY=axis==='y'&&!needsY(settings);
  const choices=axis==='x'?(settings.kind==='histogram'||settings.kind==='wafer'?numeric:columns):(['scatter','box','wafer'].includes(settings.kind)||(['bar','line','kpi'].includes(settings.kind)&&['avg','sum'].includes(settings.aggregate))?numeric:columns);
  const logAvailable=supportsLogAxis(settings,axis,columns);
  const logarithmic=settings.options[`${axis}_scale`]==='log';
  const rangeVisible=autoY||(showsMeasure&&settings.value_expression!=null)||columns.some(c=>c.name===settings[axis]&&isNumeric(c));
  return <>
   {showsMeasure?valueField:autoY?<div className="visual-auto-value"><Hash size={16}/>{yLabel}</div>:<DataField label={`${axis.toUpperCase()}축 데이터`} value={settings[axis]} columns={choices} onChange={value=>settingsPatch({[axis]:value,options:{...settings.options,[`${axis}_label`]:value===settings[axis]?settings.options[`${axis}_label`]:undefined}})}/>}
   {showAxes&&(axis==='x'||(settings.y&&(!showsMeasure||settings.value_expression==null)))&&<TextField fullWidth label={`${axis.toUpperCase()}축 표시 이름`} placeholder={settings[axis]||'컬럼명 그대로'} value={settings.options[`${axis}_label`]??''} inputProps={{maxLength:80}} onChange={e=>settingsOption({[`${axis}_label`]:e.target.value})}/>}
   {showsMeasure&&<div className="visual-source-detail">{valueSource(settings,columns).detail}</div>}
   {(logAvailable||showAxes)&&<div className="visual-axis-presentation">
    {logAvailable&&<TextField select fullWidth label={`${axis.toUpperCase()}축 스케일`} value={settings.options[`${axis}_scale`]??'linear'} onChange={e=>setSettings(changeAxisScale(settings,axis,e.target.value as AxisScale))}><MenuItem value="linear">선형</MenuItem><MenuItem value="log">로그 (밑 10)</MenuItem></TextField>}
    {showAxes&&<div className="visual-font-setting"><div className="visual-font-label"><span>글자 크기</span><strong>{settings.options[`${axis}_font_size`]??10}px</strong></div><Slider min={8} max={32} step={1} value={settings.options[`${axis}_font_size`]??10} aria-label={`${axis.toUpperCase()}축 글자 크기`} valueLabelDisplay="auto" onChange={(_,value)=>settingsOption({[`${axis}_font_size`]:value as number})}/></div>}
   </div>}
   {logAvailable&&logarithmic&&<small className="visual-scale-note">0 이하 값은 로그 축에 표시되지 않습니다.</small>}
   {showAxes&&<>
   {(rangeVisible||(axis==='x'&&['bar','line'].includes(settings.kind))||showsMeasure)&&<details className="visual-axis-details"><summary>세부 설정<ChevronDown size={15}/></summary><div>
    {showsMeasure&&(valueAggregates(settings,columns).length>1||formatField)&&<div className="visual-field-row">{valueAggregates(settings,columns).length>1&&aggregateField}{formatField}</div>}
    {rangeVisible&&<div className="visual-range-fields">{(['min','max'] as const).map(bound=><TextField key={bound} label={bound==='min'?'최솟값':'최댓값'} placeholder="자동" type="number" error={logarithmic&&settings.options[`${axis}_${bound}`]!=null&&settings.options[`${axis}_${bound}`]!<=0} helperText={logarithmic?'0보다 큰 실제 값':undefined} value={settings.options[`${axis}_${bound}`]??''} onChange={e=>settingsOption({[`${axis}_${bound}`]:e.target.value===''?undefined:Number(e.target.value)})}/>)}</div>}
    {axis==='x'&&['bar','line'].includes(settings.kind)&&<TextField select fullWidth label="정렬" value={settings.options.sort??'asc'} onChange={e=>settingsOption({sort:e.target.value as 'asc'|'desc'})}><MenuItem value="asc">오름차순</MenuItem><MenuItem value="desc">내림차순</MenuItem></TextField>}
   </div></details>}</>}
   {!showAxes&&calculationDetails}
  </>;
 };
 return <Dialog open TransitionProps={{onEntered:()=>{if(focusValue||recoveredExpression){const anchor=(hasCellValue(initial.kind)?valueButton:yButton).current;if(anchor){open(hasCellValue(initial.kind)?'value':'y',anchor);if(recoveredExpression)setExpressionDraft(recoveredExpression)}}}}} onClose={saving?undefined:onClose} fullWidth maxWidth="lg" PaperProps={{className:'visual-card-dialog'}} aria-labelledby="visual-card-heading">
  <div className="dialog-heading"><div className="dialog-icon"><SlidersHorizontal size={21}/></div><div><h2 id="visual-card-heading">카드 구성</h2></div><IconButton onClick={onClose} disabled={saving} aria-label="닫기"><X/></IconButton></div>
  <Tabs value="simple" aria-label="카드 편집 방식" sx={{px:3}}><Tab value="simple" label="간편 편집기"/><Tab value="python" label="Python 스크립트" disabled={!dataset||saving||!!popup} onClick={openPython}/></Tabs>
  {draftNotice&&<div style={{padding:'0 24px'}}>{draftNotice}</div>}
  <DialogContent className="visual-editor-content"><fieldset disabled={saving} style={{display:'contents'}}><aside className="visual-card-setup">
   <TextField select fullWidth label="데이터셋" value={draft.dataset_id} disabled={saving} onChange={e=>{if(saving)return;const d=datasets.find(d=>d.id===e.target.value);if(d){onEdited?.();setDraft(changeDataset(draft,d));setExpressionDraft('');setError(null)}}}>
    {datasets.map(d=><MenuItem key={d.id} value={d.id}><DatasetLabel dataset={d}/></MenuItem>)}
   </TextField>
   <div className="visual-section-label">차트 종류</div>
   <div className="visual-kind-grid" role="group" aria-label="차트 종류">{kinds.map(k=><button type="button" className={`visual-kind ${draft.kind===k.value?'selected':''}`} aria-pressed={draft.kind===k.value} key={k.value} onClick={()=>{onEdited?.();if(k.value==='gantt'){onGantt({...draft,kind:'gantt'});return}if(k.value!==draft.kind){setDraft(changeKind(draft,k.value,columns));setExpressionDraft('');setPopup(null);setError(null)}}}><k.icon size={21}/><span>{k.name}</span>{draft.kind===k.value&&<Check size={12} className="visual-kind-check"/>}</button>)}</div>
   <details className="visual-advanced"><summary><SlidersHorizontal size={16}/><span>세부 설정</span>{draft.filters.length>0&&<small>필터 {draft.filters.length}</small>}<ChevronDown size={16}/></summary><div className="visual-advanced-content">
    {supportsTrellis(draft.kind)&&<>
     <DataField label="패널 구분 (Trellis)" value={draft.trellis} columns={columns.filter(c=>!/^(jsonb?|bytea|record|array)$|\[\]$/i.test(c.type))} onChange={trellis=>patch({trellis})} optional/>
     {draft.trellis&&<><TextField select fullWidth label="한 줄의 패널 수" value={draft.options.trellis_columns??0} onChange={e=>patch({options:{...draft.options,trellis_columns:Number(e.target.value)}})}><MenuItem value={0}>자동</MenuItem>{[1,2,3,4].map(n=><MenuItem key={n} value={n}>{n}개</MenuItem>)}</TextField><small>같은 축 범위로 비교합니다. 최대 12개 패널까지 표시합니다.</small></>}
    </>}
    <span className="visual-section-label">필터</span><CardPeriodControls card={draft} context={context} onChange={period=>patch({period})}/><FilterBuilder columns={columns} filters={draft.filters} onChange={filters=>patch({filters})}/>
   </div></details>
   <ErrorNotice error={latest.error}/>
  </aside><section className="visual-canvas-column" aria-label="카드 미리보기">
   <div className="visual-canvas-heading"><span><MousePointer2 size={15}/>차트의 제목·축·색상을 눌러 편집하세요</span><IconButton aria-label="미리보기 새로고침" size="small" disabled={!ready} onClick={()=>preview.refetch()}><RefreshCw size={16}/></IconButton></div>
   <div className="visual-chart-card">
    <div className="visual-chart-header"><button type="button" className="visual-title-button" aria-label="카드 제목 편집" onClick={e=>open('title',e.currentTarget)}><span>{liveCard.title||'제목 입력'}</span><Pencil size={15}/></button>
     {liveCard.kind!=='kpi'&&<button type="button" ref={styleButton} className={`visual-style-button ${part==='style'?'active':''}`} aria-label="색상과 스타일 설정" aria-haspopup="dialog" onClick={e=>open('style',e.currentTarget)}><Palette size={16}/><span>{hasGroups(liveCard.kind)?liveCard.color||'색상':'색상'}</span><span className="visual-mini-palette">{(palettes[liveCard.options.palette??'teal']??palettes.teal).slice(0,3).map(c=><i key={c} style={{background:c}}/>)}</span></button>}
    </div>
     {cellValue&&<button type="button" ref={valueButton} className={`visual-data-source ${part===(cellValue?'value':'y')?'active':''}`} aria-label={cellValue?'셀 값 데이터 설정':'표시 데이터 설정'} aria-haspopup="dialog" onClick={e=>open(cellValue?'value':'y',e.currentTarget)}><span className="visual-data-role">{cellValue?'셀 값':'표시 데이터'}</span><strong>{source.label}</strong><small>{source.detail}</small><Pencil size={14}/></button>}
    <div className={`visual-plot-stage ${showAxes?'':'is-kpi'}`}>
     <div className="visual-plot-content" aria-busy={!issue&&!ready}>
      {liveCard.kind==='kpi'?<button type="button" ref={yButton} className="visual-kpi-button" aria-label="값 설정" onClick={e=>open('y',e.currentTarget)}><strong>{value==null?'—':Number(value).toLocaleString('ko-KR',{maximumFractionDigits:2})}{value!=null&&isPercent(liveCard)?'%':''}</strong><span>{issue?'값 데이터 선택':!result?'미리보기 업데이트 중':yLabel}<Pencil size={16}/></span></button>:!dataset?<Loading label="데이터셋을 확인하는 중"/>:issue?<div className="visual-empty-grid"><span>{issue}</span></div>:!current||preview.isPending?<Loading label="미리보기 업데이트 중"/>:preview.error?<div className="visual-preview-error"><ErrorNotice error={preview.error}/><Button onClick={()=>preview.refetch()}>다시 시도</Button></div>:!result?.rows.length?<div className="visual-empty-grid"><span>조건에 맞는 데이터가 없습니다.</span></div>:<Plot card={liveCard} result={result} editing onEdit={openPlotPart}/>}
     </div>
     {showAxes?<>
      <button type="button" ref={xButton} className={`visual-axis-button visual-x-axis ${part==='x'?'active':''}`} aria-label="X축 설정" aria-haspopup="dialog" onClick={e=>open('x',e.currentTarget)}><span className="visual-axis-tag">X</span><span style={{fontSize:liveCard.options.x_font_size??10}}>{axisLabel(liveCard,'x')||'데이터 선택'}</span>{liveCard.options.x_scale==='log'&&<small>log</small>}<Pencil size={13}/></button>
      <button type="button" ref={yButton} className={`visual-axis-button visual-y-axis ${part==='y'?'active':''}`} aria-label="Y축 설정" aria-haspopup="dialog" onClick={e=>open('y',e.currentTarget)}><span className="visual-axis-tag">Y</span><span style={{fontSize:liveCard.options.y_font_size??10}}>{yLabel}</span>{liveCard.options.y_scale==='log'&&<small>log</small>}<Pencil size={13}/></button>
     </>:null}
    </div>
    <div className="visual-preview-status" role="status"><span className={`visual-status-dot ${ready?'ready':''}`}/>{issue?'설정을 선택하면 미리보기가 표시됩니다.':!current||preview.isFetching?'업데이트 중':preview.error?'미리보기를 확인해 주세요.':result?`${result.meta.mode==='sample'?'표본':result.meta.mode==='aggregate'?'집계':'조회'} ${result.meta.returned_rows.toLocaleString()}행 · 자동 미리보기`:'미리보기 준비 중'}</div>
   </div>
   {liveCard.kind==='kpi'&&preview.error&&<div><ErrorNotice error={preview.error}/><Button onClick={()=>preview.refetch()}>다시 시도</Button></div>}
   {result&&logScaleWarning(liveCard,result.rows)&&<Alert severity="info">{logScaleWarning(liveCard,result.rows)}</Alert>}
   {result?.meta.truncated===true&&<Alert severity="warning">표시 한도로 일부 결과가 생략되었습니다. 필터를 좁혀주세요.</Alert>}
   {!!result?.meta.ignored_filters?.length&&<Alert severity="info">미적용 필터: {result.meta.ignored_filters.join(', ')}</Alert>}
   {liveCard.kind==='wafer'&&<div className="visual-context-note">{liveCard.trellis?'각 패널에 하나의 Wafer가 표시됩니다. 여러 Wafer를 비교하려면 wafer_id로 구분하고 필터 범위를 조절하세요.':'Wafer 필터로 하나를 선택하거나, 세부 설정의 패널 구분에서 wafer_id를 선택해 여러 Wafer를 비교하세요.'}</div>}
   <ErrorNotice error={error}/>
  </section></fieldset></DialogContent>
  <DialogActions className="visual-editor-actions"><span>저장 전까지 변경 내용은 이 창에만 반영됩니다.</span><Button color="secondary" onClick={onClose} disabled={saving}>취소</Button><Button variant="contained" startIcon={<Save size={16}/>} onClick={save} disabled={saving||!!popup||!draft.title.trim()||!ready}>{saving?'저장 중…':'카드 저장'}</Button></DialogActions>
  <Popover action={popoverAction} open={!!popup} anchorEl={popup?.anchor} onClose={closePopup} anchorOrigin={{vertical:'bottom',horizontal:'center'}} transformOrigin={{vertical:'top',horizontal:'center'}} marginThreshold={12} slotProps={{paper:{ref:setPopoverPaper,className:`visual-settings-popover ${settings.value_expression!=null&&(part==='value'||part==='y')?'has-expression':''}`,role:'dialog','aria-label':part?(part==='y'&&settings.kind==='kpi'?'값 설정':`${partNames[part]} 설정`):undefined}}}>
   {part&&<><div className="visual-popover-heading"><strong>{part==='y'&&settings.kind==='kpi'?'값 설정':partNames[part]}</strong><IconButton aria-label="설정 닫기" onClick={closePopup} size="small"><X size={18}/></IconButton></div><div className="visual-popover-fields">
    {part==='title'&&<TextField fullWidth autoFocus label="카드 제목" value={settings.title} inputProps={{maxLength:200}} onChange={e=>settingsPatch({title:e.target.value})}/>}
    {(part==='x'||part==='y')&&axisPopup(part)}
    {part==='value'&&<>{valueField}<div className="visual-source-detail">{valueSource(settings,columns).detail}</div>{calculationDetails}</>}
    {part==='style'&&<>
     {hasGroups(settings.kind)&&<DataField label="색상으로 구분할 데이터" value={settings.color} columns={columns} onChange={color=>settingsPatch({color})} optional/>}
     <span className="visual-section-label">색상 팔레트</span><div className="visual-palette-grid">{[['teal','청록'],['viridis','보라 · 초록'],['blues','파랑'],['warm','따뜻한 색']].map(([id,name])=><button type="button" key={id} aria-label={`${name} 팔레트`} aria-pressed={(settings.options.palette??'teal')===id} className={(settings.options.palette??'teal')===id?'selected':''} onClick={()=>settingsOption({palette:id})}><span>{palettes[id].map(c=><i key={c} style={{background:c}}/>)}</span><small>{name}</small></button>)}</div>
     {settings.kind==='histogram'&&<><div className="visual-font-label"><span>구간 수</span><strong>{settings.options.bins??30}</strong></div><Slider min={5} max={100} step={1} value={settings.options.bins??30} aria-label="히스토그램 구간 수" valueLabelDisplay="auto" onChange={(_,value)=>settingsOption({bins:value as number})}/></>}
    </>}
   </div><div className="visual-popover-actions"><Button color="secondary" onClick={closePopup}>취소</Button><Button variant="contained" onClick={applyPopup} disabled={!!popupIssue}>완료</Button></div></>}
  </Popover>
 </Dialog>;
}

type CardEditorProps={initial:CardSpec;datasets:Dataset[];context:QueryContext;onClose:()=>void;onSave:(card:CardSpec,expectedVersion?:number)=>Promise<void>;focusValue?:boolean;draftTargetId?:string;baseVersion?:number;onLoadLatest?:(cardId:string)=>Promise<LatestCardDraft>};
export default function CardEditor(props:CardEditorProps){
 const [initial,setInitial]=useState(props.initial),[epoch,setEpoch]=useState(0),[baseVersion,setBaseVersion]=useState(props.baseVersion??null),[value,setValue]=useState<CardEditDraft>({card:props.initial,expressionDraft:''}),[restoredExpression,setRestoredExpression]=useState(''),[conflict,setConflict]=useState<LatestCardDraft|null>(null),[editorBusy,setEditorBusy]=useState(false);
 const missingInitial=!!initial.dataset_id&&!props.datasets.some(d=>d.id===initial.dataset_id);
 const previousDataset=useQuery({queryKey:['versions',props.context.project_id??null,initial.dataset_id,'latest'],queryFn:()=>api<Dataset>(`/datasets/${encodeURIComponent(initial.dataset_id)}/latest${props.context.project_id!=null?`?project_id=${props.context.project_id}`:''}`),enabled:missingInitial});
 const editorDatasets=missingInitial&&previousDataset.data?[...props.datasets,previousDataset.data]:props.datasets;
 const localDraft=useLocalDraft({kind:'card',targetId:props.draftTargetId??props.initial.id,projectId:props.context.project_id??null,baseVersion,value,validate:(candidate)=>validCardDraft(candidate)&&(props.draftTargetId==='new'||candidate.card.id===props.initial.id)});
 const receive=useCallback((next:CardEditDraft)=>setValue(previous=>JSON.stringify(previous)===JSON.stringify(next)?previous:next),[]);
 function replace(next:CardEditDraft,version:number|null){setInitial(next.card);setValue(next);setRestoredExpression(next.expressionDraft);setBaseVersion(version);setEpoch(v=>v+1)}
 function switchEditor(card:CardSpec){localDraft.markEdited();setInitial(card);setValue({card,expressionDraft:''});setRestoredExpression('');setEpoch(v=>v+1)}
 async function save(card:CardSpec){if(localDraft.blocked)throw new Error('보관한 초안을 먼저 비교하거나 버린 뒤 저장해 주세요.');try{await props.onSave(card,baseVersion??undefined);localDraft.clearSaved()}catch(error){if(error instanceof ApiError&&error.status===409&&props.onLoadLatest){try{setConflict(await props.onLoadLatest(card.id))}catch{throw new Error('최신 보드를 불러오지 못했습니다. 내 편집 내용은 보관되며 저장은 중지했습니다.')}}throw error}}
 const notice=<LocalDraftNotice disabled={editorBusy} draft={localDraft} onRestore={record=>replace(record.value,typeof record.baseVersion==='number'?record.baseVersion:null)}/>;
 const common={...props,datasets:editorDatasets,initial,onSave:save,draftNotice:notice,onEdited:localDraft.markEdited,onDraftChange:receive,onBusyChange:setEditorBusy,recoveredExpression:restoredExpression};
 if(missingInitial&&!previousDataset.data)return <Dialog open onClose={props.onClose}><DialogContent>{previousDataset.error?<ErrorNotice error={previousDataset.error}/>:<Loading label="기존 카드의 데이터셋을 확인하는 중"/>}</DialogContent><DialogActions><Button onClick={props.onClose}>닫기</Button></DialogActions></Dialog>;
 return <>{initial.editor_mode==='python'?<PythonCardEditor key={epoch} {...common} onSimple={switchEditor}/>:initial.kind==='gantt'?<GanttEditor key={epoch} {...common} onOther={card=>switchEditor({...card,kind:'bar',gantt:undefined,x:props.datasets.find(d=>d.id===card.dataset_id)?.columns[0]?.name,aggregate:'count'})}/>:<StandardCardEditor key={epoch} {...common} onGantt={switchEditor} onPython={switchEditor}/>}
  <DraftConflictDialog open={!!conflict} title="카드 변경 내용 비교" mine={cardDraftComparison(value.card)} latest={props.draftTargetId==='new'&&!conflict?.card?'최신 보드에 아직 저장되지 않은 새 카드입니다.':cardDraftComparison(conflict?.card??null)} onClose={()=>setConflict(null)} onUseLatest={()=>{if(!conflict)return;localDraft.discard();if(conflict.card)replace({card:conflict.card,expressionDraft:''},conflict.version);else props.onClose();setConflict(null)}} onKeepMine={conflict&&(conflict.card||props.draftTargetId==='new')?()=>{localDraft.markEdited();replace(value,conflict.version);setConflict(null)}:undefined}/>
 </>;
}
