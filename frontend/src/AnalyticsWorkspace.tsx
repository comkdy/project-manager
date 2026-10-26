import {useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Autocomplete,Button,Checkbox,FormControlLabel,IconButton,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ArrowRight,BarChart3,ChartNoAxesCombined,ChevronLeft,ChevronRight,Clock3,GitCompareArrows,Inbox,Play,Plus,RefreshCw,ScanLine,Trash2} from 'lucide-react';
import {ErrorNotice,Loading} from './components';
import {createAnalysisRun,loadAnalysisCatalog,loadAnalysisRun,loadAnalysisRuns} from './analyticsApi';
import type {AnalysisCatalog,AnalysisPoint,AnalysisRequest,AnalysisResultSeries,AnalysisRun,AnalysisSeries,AnalysisStatistics,ApprovedAnalysisDefinition,RetestPolicy} from './analyticsTypes';
import type {DefinitionRef} from './semanticTypes';
import {detailFields} from './semanticTypes';
import {approvedAnalysisDefinitions,createAnalysisRestoreGuard,type AnalysisResume} from './analysisSelection';
import {analysisDateInput,analysisRequestDate} from './analysisDates';
import './analytics.css';
import JobActionButtons from './JobActionButtons';
import {ScheduleAnalysisButton} from './AnalysisSchedules';
import './chat-knowledge.css';
import {ApiError} from './api';
import {loadAnalysisProposal,confirmAnalysisProposal} from './analysisProposalApi';
import AnalysisBrief from './AnalysisBrief';
import AnalysisPaths from './AnalysisPaths';
import {analysisPaths,resultTitle,runProject,type AnalysisFocus,type AnalysisStart} from './analysisExperience';
import type {Project} from './types';
import type {AnalysisProposalView} from './analysisProposal.generated';
import {analysisConditionNames,proposalConfirmBody,proposalEditorRequest,proposalFixedTargetLabel,proposalReadyToConfirm,proposalRemainingBlockers,type AnalysisProposalSelection} from './analysisProposalState';

type Mode='statistics'|'changes'|'trend'|'split';
interface SeriesCriterionInput {id:number;series:AnalysisSeries|null;minimum:string;delta:string}
const modes=[
 {id:'statistics' as const,title:'전체 PARA 통계',icon:BarChart3,description:'등록된 Inline·EPM 전체 PARA의 분포와 측정 현황을 확인합니다.'},
 {id:'changes' as const,title:'변화 탐지',icon:ScanLine,description:'두 기간의 전체 PARA를 비교하고, 지정한 기준에 해당하는 변화를 찾습니다.'},
 {id:'trend' as const,title:'단일 PARA 추세',icon:ChartNoAxesCombined,description:'하나의 PARA를 선택해 일별 평균과 두 기간의 차이를 확인합니다.'},
 {id:'split' as const,title:'split 비교',icon:GitCompareArrows,description:'선택한 공정의 recipe 이력으로 그룹을 나누고 이후 Inline·EPM 측정값을 비교합니다.'},
];
const statuses={queued:'대기 중',running:'분석 중',cancelling:'취소 요청됨',cancelled:'취소 완료',completed:'완료',failed:'실패',needs_input:'기준 확인 필요'};
const resultStatuses={needs_criteria:'기준 확인 필요',insufficient_data:'표본 부족',changed:'변화 감지',stable:'기준 미만'};
const familyName=(value:string)=>value==='inline'?'Inline':'EPM';
const seriesName=(s:AnalysisSeries)=>`${familyName(s.family)}${s.operation?` · ${s.operation}`:''} · ${s.parameter}`;
const seriesKey=(s:AnalysisSeries)=>JSON.stringify([s.family,s.operation??'',s.parameter]);
const formatNumber=(value:number|null|undefined)=>value==null||!Number.isFinite(value)?'—':value.toLocaleString('ko-KR',{maximumFractionDigits:5});
const formatDate=(value:string|null|undefined)=>value?new Date(value).toLocaleString('ko-KR'):'—';
const shortDate=(value:string)=>new Date(value).toLocaleDateString('ko-KR',{month:'numeric',day:'numeric'});
const busyRun=(run:AnalysisRun|undefined)=>!!run&&['queued','running','cancelling'].includes(run.status);
const runName=(run:AnalysisRun)=>`${run.request.purpose==='validation'?'검증 · ':''}${run.kind==='statistics'?'전체 PARA 통계':run.kind==='split'?'split 비교':run.request.parameter?'단일 PARA 추세':'변화 탐지'}`;

type WorkspaceProps={onStart?:(focus:AnalysisFocus)=>void;start?:AnalysisStart|null;projectId?:number|null;projects?:Project[];onRecords?:()=>void;onOpenKnowledge?:(run?:AnalysisRun,questionId?:string)=>void;onSelectRun?:(id:string)=>void;resume?:AnalysisResume|null;canRun?:boolean;workspace?:string;initialRunId?:string|null;visible?:boolean;proposal?:AnalysisProposalSelection|null;onExitProposal?:()=>void;onProposalConfirmed?:(run:AnalysisRun,proposalId:string)=>void;onProposalResult?:(runId:string)=>void};
export default function AnalyticsWorkspace(props:WorkspaceProps){
 const [opened,setOpened]=useState<AnalysisProposalSelection[]>([]);
 const current=props.proposal;
 const proposals=current?(opened.some(entry=>entry.proposal.proposal_id===current.proposal.proposal_id)?opened.map(entry=>entry.proposal.proposal_id===current.proposal.proposal_id?current:entry):[...opened,current]):opened;
 useEffect(()=>{if(current)setOpened(previous=>previous.some(entry=>entry.proposal.proposal_id===current.proposal.proposal_id)?previous.map(entry=>entry.proposal.proposal_id===current.proposal.proposal_id?current:entry):[...previous,current])},[current]);
 return <><div hidden={!!current}><AnalyticsWorkspaceView {...props} proposal={undefined} visible={props.visible!==false&&!current}/></div>{proposals.map(entry=><div hidden={entry.proposal.proposal_id!==current?.proposal.proposal_id} key={entry.proposal.proposal_id}><AnalyticsWorkspaceView {...props} initialRunId={null} resume={null} onSelectRun={undefined} proposal={undefined} initialProposal={entry.proposal} proposalEntryToken={entry.token} visible={props.visible!==false&&entry.proposal.proposal_id===current?.proposal.proposal_id}/></div>)}</>;
}

function AnalyticsWorkspaceView({onStart,start:entry,projectId=null,projects=[],onRecords,onOpenKnowledge,onSelectRun,resume,canRun=true,workspace='current',initialRunId=null,visible=true,initialProposal,proposalEntryToken,onExitProposal,onProposalConfirmed,onProposalResult}:WorkspaceProps&{initialProposal?:AnalysisProposalView;proposalEntryToken?:number}){
 const client=useQueryClient();
 const formRef=useRef<HTMLFormElement>(null);
 const restoreGuard=useRef(createAnalysisRestoreGuard()),restoreContext=useRef<{selected:string|null;visible:boolean}>({selected:null,visible});
 const [restoring,setRestoring]=useState<number|null>(null);
 const nextCriterionId=useRef(0);
 const [reviewedProposal,setReviewedProposal]=useState<AnalysisProposalView|null>(initialProposal??null);
 const proposalMode=!!initialProposal;
 const proposalQuery=useQuery({queryKey:['analytics','proposal',workspace,initialProposal?.proposal_id,initialProposal?.project_id??null],queryFn:({signal})=>loadAnalysisProposal(initialProposal!.proposal_id,initialProposal!.project_id??null,signal),enabled:proposalMode&&visible,retry:false,refetchOnWindowFocus:false});
 useEffect(()=>{if(initialProposal&&visible)void proposalQuery.refetch()},[proposalEntryToken]);
 const latestProposal=proposalQuery.data;
 const proposalReady=proposalReadyToConfirm(reviewedProposal,latestProposal,workspace,proposalQuery);
 const [focus,setFocus]=useState<AnalysisFocus>(entry?.focus??'epm'),[requestProject,setRequestProject]=useState<number|null>(projectId),[showForm,setShowForm]=useState(!!entry&&!initialRunId);
 const [mode,setMode]=useState<Mode>('split'),[selected,setSelected]=useState<string|null>(initialRunId),[submitting,setSubmitting]=useState(false),[error,setError]=useState<unknown>(null);
 restoreContext.current={selected,visible};
 useEffect(()=>{restoreGuard.current.invalidate();setRestoring(null)},[selected,visible]);
 useEffect(()=>{if(initialRunId){setSelected(initialRunId);setShowForm(false)}},[initialRunId]);
 const [series,setSeries]=useState<AnalysisSeries|null>(null),[operation,setOperation]=useState(''),[recipeA,setRecipeA]=useState(''),[recipeB,setRecipeB]=useState('');
 const [start,setStart]=useState(''),[end,setEnd]=useState(''),[baselineStart,setBaselineStart]=useState(''),[baselineEnd,setBaselineEnd]=useState(''),[currentStart,setCurrentStart]=useState(''),[currentEnd,setCurrentEnd]=useState('');
 const [restoredDates,setRestoredDates]=useState<Partial<Pick<AnalysisRequest,'start'|'end'|'baseline_start'|'baseline_end'|'current_start'|'current_end'>>>({});
 const [retest,setRetest]=useState<RetestPolicy|''>(''),[minimum,setMinimum]=useState(''),[delta,setDelta]=useState(''),[lotsA,setLotsA]=useState(''),[lotsB,setLotsB]=useState('');
 const [definitions,setDefinitions]=useState<ApprovedAnalysisDefinition[]>([]),[references,setReferences]=useState<DefinitionRef[]>([]);
 const [restoredNotice,setRestoredNotice]=useState('');
 const [purpose,setPurpose]=useState<'analysis'|'validation'>('analysis');
 const [scopeProject,setScopeProject]=useState<AnalysisRequest['_project_scope']>();
 const [seriesCriteria,setSeriesCriteria]=useState<SeriesCriterionInput[]>([]);
 const catalog=useQuery({queryKey:['analytics','catalog',workspace],queryFn:loadAnalysisCatalog,enabled:visible});
 const history=useQuery({queryKey:['analytics','runs',workspace],queryFn:loadAnalysisRuns,enabled:visible,refetchInterval:q=>visible&&q.state.data?.some(busyRun)?2000:false});
 const detail=useQuery({queryKey:['analytics','run',workspace,selected],queryFn:()=>loadAnalysisRun(selected!),enabled:!!selected&&visible,refetchInterval:q=>visible&&busyRun(q.state.data)?2000:false});
 const active=detail.data??history.data?.find(r=>r.id===selected);
 const viewedRun=useRef<string|null>(null);
 useEffect(()=>{if(active&&viewedRun.current!==active.id){viewedRun.current=active.id;if(active.request.family)setFocus(active.request.family)}},[active?.id]);
 useEffect(()=>{if(selected)onSelectRun?.(selected)},[selected,onSelectRun]);
 useEffect(()=>{if(detail.data&&!busyRun(detail.data))void client.invalidateQueries({queryKey:['analytics','runs',workspace]})},[detail.data?.status,client,workspace]);
 const currentMode=modes.find(m=>m.id===mode)!;
 const recipes=catalog.data?.recipe_operations.find(item=>item.operation===operation)?.recipes??[];
 const missingRequired=mode==='statistics'?!!start!==!!end:!retest||(mode==='split'?!(start&&end&&operation&&recipeA&&recipeB):!(baselineStart&&baselineEnd&&currentStart&&currentEnd)||(mode==='trend'&&!series));
 const remainingBlockers=proposalRemainingBlockers(reviewedProposal,mode==='statistics'?!!start===!!end:mode==='split'?!!(start&&end):!!(baselineStart&&baselineEnd&&currentStart&&currentEnd));
 const fixedTargetLabel=proposalFixedTargetLabel(reviewedProposal);
 const busy=submitting||!!history.data?.some(busyRun)||busyRun(active);
 function changeMode(next:Mode){
  restoreGuard.current.invalidate();
  setMode(next);setError(null);setDefinitions([]);setReferences([]);setRestoredNotice('');
  if(purpose==='validation'){setStart('');setEnd('');setBaselineStart('');setBaselineEnd('');setCurrentStart('');setCurrentEnd('');setRetest('');setMinimum('');setDelta('');setOperation('');setRecipeA('');setRecipeB('');setLotsA('');setLotsB('');setSeries(null);setSeriesCriteria([])}
  setPurpose('analysis');
 }
 function restoreRequest(q:AnalysisRequest,approved:ApprovedAnalysisDefinition[]=[]){
  setRequestProject(q.project_id??q._project_scope?.id??null);setShowForm(true);if(q.family)setFocus(q.family);
  setScopeProject(q._project_scope);
  setMode(q.kind==='trend'?(q.parameter?'trend':'changes'):q.kind);setPurpose(q.purpose??'analysis');
  setSeries(q.parameter&&q.family?{family:q.family,operation:q.operation??null,parameter:q.parameter}:null);
  setOperation(q.kind==='split'?q.operation??'':'');setRecipeA(q.recipe_a??'');setRecipeB(q.recipe_b??'');
  setRestoredDates({start:q.start,end:q.end,baseline_start:q.baseline_start,baseline_end:q.baseline_end,current_start:q.current_start,current_end:q.current_end});
  setStart(analysisDateInput(q.start));setEnd(analysisDateInput(q.end));setBaselineStart(analysisDateInput(q.baseline_start));setBaselineEnd(analysisDateInput(q.baseline_end));setCurrentStart(analysisDateInput(q.current_start));setCurrentEnd(analysisDateInput(q.current_end));
  const singleCriterion=q.kind==='trend'&&q.parameter?q.series_criteria?.find(item=>item.family===q.family&&(item.operation??'')===(q.operation??'')&&item.parameter===q.parameter):undefined;
  const effectiveMinimum=singleCriterion?.min_samples??q.min_samples,effectiveDelta=singleCriterion?.absolute_delta??q.absolute_delta;
  setRetest(q.retest_policy??'');setMinimum(effectiveMinimum==null?'':String(effectiveMinimum));setDelta(effectiveDelta==null?'':String(effectiveDelta));setLotsA(q.lot_ids_a?.join('\n')??'');setLotsB(q.lot_ids_b?.join('\n')??'');setError(null);
  setSeriesCriteria(q.kind==='trend'&&q.parameter?[]:(q.series_criteria??[]).map(item=>({id:nextCriterionId.current++,series:{family:item.family,operation:item.operation,parameter:item.parameter},minimum:String(item.min_samples),delta:String(item.absolute_delta)})));
  setDefinitions(approved);setReferences(q.knowledge_refs??[]);
  formRef.current?.scrollIntoView({block:'start'});
 }
 function restore(run:AnalysisRun){restoreRequest(run.request,approvedAnalysisDefinitions(run))}
 useEffect(()=>{if(entry&&!proposalMode&&!initialRunId){restoreRequest({kind:'split',project_id:entry.projectId});setFocus(entry.focus);setSelected(null);setRestoredNotice('')}},[entry?.token]);
 useEffect(()=>{if(initialProposal){const request=proposalEditorRequest(initialProposal);if(request)restoreRequest(request)}},[initialProposal?.proposal_id]);
 function reloadProposal(){if(!latestProposal?.request)return;const request=proposalEditorRequest(latestProposal);if(!request)return;setReviewedProposal(latestProposal);restoreRequest(request);setError(null)}
 useEffect(()=>{if(resume){restoreGuard.current.invalidate();setSelected(resume.run.id);restore(resume.run);setRestoredNotice('원래 분석 조건을 불러왔습니다. 현재 승인된 설명을 확인하고 미정 조건을 직접 입력한 뒤 실행하세요.')}},[resume?.token]);
 async function restoreCurrent(){
  if(!active)return;
  const ticket=restoreGuard.current.start(active.id);setRestoring(ticket.generation);
  const current=()=>restoreGuard.current.accepts(ticket,restoreContext.current.selected,restoreContext.current.visible);
  try{const run=await loadAnalysisRun(active.id);if(!current())return;client.setQueryData(['analytics','run',workspace,run.id],run);restore(run)}catch(e){if(current())setError(e)}finally{setRestoring(value=>value===ticket.generation?null:value)}
 }
 async function submit(){
  if(submitting||!canRun||proposalMode&&!proposalReady)return;
  setError(null);
  const pairs=mode==='statistics'||mode==='split'?[[start,end]]:[[baselineStart,baselineEnd],[currentStart,currentEnd]];
  if(pairs.some(([a,b])=>a&&b&&new Date(a)>=new Date(b))){setError(new Error('종료 시각은 시작 시각보다 뒤여야 합니다.'));return}
  if(mode==='trend'&&!series){setError(new Error('추세를 확인할 PARA를 먼저 선택해 주세요.'));return}
  if(mode==='split'&&recipeA&&recipeA===recipeB){setError(new Error('서로 다른 recipe를 선택해 주세요.'));return}
  if(mode!=='statistics'&&minimum&&(!Number.isInteger(Number(minimum))||Number(minimum)<1||Number(minimum)>1000000)){setError(new Error('최소 유효 표본 수는 1부터 1,000,000 사이의 정수로 입력해 주세요.'));return}
  if(mode!=='statistics'&&delta&&(!Number.isFinite(Number(delta))||Number(delta)<0)){setError(new Error('평균 변화량 기준은 0 이상의 수로 입력해 주세요.'));return}
  const request:AnalysisRequest={kind:mode==='changes'?'trend':mode,purpose,project_id:requestProject};
  if((mode==='changes'||mode==='split')&&seriesCriteria.length){
   const seen=new Set<string>();
   for(const [index,criterion] of seriesCriteria.entries()){
    if(!criterion.series||!criterion.minimum||!criterion.delta){setError(new Error(`${index+1}번째 PARA별 기준의 PARA, 최소 표본 수, 변화량을 모두 입력하거나 해당 행을 삭제해 주세요.`));return}
    if(!Number.isInteger(Number(criterion.minimum))||Number(criterion.minimum)<1||Number(criterion.minimum)>1000000||!Number.isFinite(Number(criterion.delta))||Number(criterion.delta)<0){setError(new Error(`${index+1}번째 PARA별 기준: 최소 표본 수는 1부터 1,000,000 사이의 정수, 변화량은 0 이상으로 입력해 주세요.`));return}
    const key=seriesKey(criterion.series);if(seen.has(key)){setError(new Error(`${seriesName(criterion.series)}의 기준이 중복되었습니다.`));return}seen.add(key);
   }
   request.series_criteria=seriesCriteria.map(criterion=>({family:criterion.series!.family,operation:criterion.series!.operation??'',parameter:criterion.series!.parameter,min_samples:Number(criterion.minimum),absolute_delta:Number(criterion.delta)}));
  }
  if(references.length)request.knowledge_refs=references;
  function putDate(key:'start'|'end'|'baseline_start'|'baseline_end'|'current_start'|'current_end',value:string){if(value)request[key]=analysisRequestDate(value,restoredDates[key])}
  if(mode==='statistics'||mode==='split'){putDate('start',start);putDate('end',end)}
  else{putDate('baseline_start',baselineStart);putDate('baseline_end',baselineEnd);putDate('current_start',currentStart);putDate('current_end',currentEnd);request.bucket='day'}
  if(mode==='trend'&&series){request.family=series.family;request.parameter=series.parameter;if(series.operation)request.operation=series.operation}
  if(mode==='split'){
   if(operation)request.operation=operation;if(recipeA)request.recipe_a=recipeA;if(recipeB)request.recipe_b=recipeB;
   const a=lotsA.split(/[\s,]+/).filter(Boolean),b=lotsB.split(/[\s,]+/).filter(Boolean);
   if(a.length)request.lot_ids_a=[...new Set(a)];if(b.length)request.lot_ids_b=[...new Set(b)];
  }
  if(mode!=='statistics'){if(retest)request.retest_policy=retest;if(minimum)request.min_samples=Number(minimum);if(delta)request.absolute_delta=Number(delta)}
  setSubmitting(true);
  try{
   const run=proposalMode?await confirmAnalysisProposal(reviewedProposal!.proposal_id,proposalConfirmBody(reviewedProposal!,request)):await createAnalysisRun(request);
   client.setQueryData(['analytics','run',workspace,run.id],run);setSelected(run.id);setShowForm(false);
   if(proposalMode){await client.invalidateQueries({queryKey:['analytics','proposal',workspace,reviewedProposal!.proposal_id]});onProposalConfirmed?.(run,reviewedProposal!.proposal_id)}
   await client.invalidateQueries({queryKey:['analytics','runs',workspace]});await client.invalidateQueries({queryKey:['workspace-activity',workspace]});await client.invalidateQueries({queryKey:['semantic','questions']});
  }catch(e){setError(e)}finally{setSubmitting(false)}
 }
 return <section className="analytics-workspace">
  <header className="analytics-heading"><div><span className="analytics-eyebrow">DATA ANALYSIS</span><h1>원인 및 영향 분석</h1><p>확인하고 싶은 관계에서 시작해, 관측된 차이와 근거를 살펴보세요.</p></div><Button startIcon={<RefreshCw size={16}/>} onClick={()=>{void catalog.refetch();void history.refetch();if(selected)void detail.refetch()}}>새로고침</Button></header>
  <ErrorNotice error={catalog.error??(!proposalMode?history.error:null)}/>
  {proposalMode&&<div className="analysis-proposal-banner"><Alert severity="info" action={<Button disabled={submitting} onClick={onExitProposal}>일반 분석 입력으로</Button>}><strong>채팅에서 받은 분석 조건 확인</strong><p>{initialProposal!.explanation}</p><p>분석 종류와 프로젝트는 이 조건안의 범위를 따릅니다. 기간·대상·처리 기준을 직접 확인하고 수정한 뒤 아래 버튼을 눌러 주세요.</p></Alert>
   {reviewedProposal?.project_scope&&<p>프로젝트: {reviewedProposal.project_scope.name} · {reviewedProposal.project_scope.lot_cds.join(', ')}</p>}
   {reviewedProposal?.display?.parameter&&reviewedProposal.request?.kind==='split'&&<p>전체 PARA를 계산하고 채팅에서는 {reviewedProposal.display.family??''} {reviewedProposal.display.parameter}를 표시하도록 요청한 조건입니다.</p>}
   {!!remainingBlockers.length&&<Alert severity="warning">확인이 필요한 항목: {remainingBlockers.map(value=>analysisConditionNames[value]??value).join(' · ')}. 수정한 조건을 서버에서 다시 확인하며, 남은 미정 항목은 확인 요청의 기준 확인으로 이어집니다.</Alert>}
   {!!reviewedProposal?.warnings?.length&&<ul>{reviewedProposal.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>}
   {!!Object.keys(reviewedProposal?.evidence??{}).length&&<details className="analysis-proposal-evidence"><summary>조건을 가져온 사용자 요청</summary><dl>{Object.entries(reviewedProposal!.evidence??{}).map(([field,quote])=><div key={field}><dt>{analysisConditionNames[field]??field}</dt><dd>{quote}</dd></div>)}</dl></details>}
   <ErrorNotice error={proposalQuery.error}/>
   {proposalQuery.isFetching&&<Loading label="현재 조건안과 실행 가능 범위를 확인하는 중"/>}
   {latestProposal&&['unavailable','cancelled'].includes(latestProposal.status)&&<Alert severity="warning">{latestProposal.explanation}</Alert>}
   {latestProposal?.status==='confirmed'&&<Alert severity="success" action={latestProposal.run_id&&<Button onClick={()=>onProposalResult?.(latestProposal.run_id!)}>분석 결과 열기</Button>}>이미 확인한 조건안입니다. 같은 분석을 다시 실행하지 않고 저장된 결과를 열 수 있습니다.</Alert>}
   {latestProposal?.status==='pending'&&latestProposal.revision!==reviewedProposal?.revision&&<Alert severity="warning" action={<Button onClick={reloadProposal}>입력 버리고 최신 조건 불러오기</Button>}>조건안의 버전이 변경되었습니다. 현재 입력은 유지했으며 실행을 중지했습니다.</Alert>}
   {(proposalQuery.isError||error instanceof ApiError&&error.status===409)&&<Button disabled={submitting||proposalQuery.isFetching} onClick={()=>void proposalQuery.refetch()}>현재 조건안 상태 다시 확인</Button>}
  </div>}
  {!proposalMode&&!active&&!showForm&&<AnalysisPaths onChoose={value=>onStart?.(value)}/>}
  {!proposalMode&&(showForm||active)&&<><div className="analysis-focus-switch">{(['epm','inline'] as const).map(value=><Button key={value} aria-pressed={focus===value} onClick={()=>{setFocus(value);if(mode==='trend'&&series?.family!==value)setSeries(null)}}>{analysisPaths[value].title}</Button>)}<Button onClick={()=>onStart?.(focus)}>새 비교 시작</Button><Button onClick={()=>setShowForm(v=>!v)}>{showForm?'조건 접기':'비교 조건 열기'}</Button><Button onClick={onRecords}>분석 기록 보기</Button></div><p>{analysisPaths[focus].description}</p></>}
  {restoredNotice&&<p className="analytics-help" role="status">{restoredNotice}</p>}
  {catalog.isPending&&<Loading label="분석할 데이터 확인 중"/>}{catalog.data&&!catalog.data.supported&&<Alert severity="warning">분석에 필요한 데이터 연결을 담당자에게 확인해 주세요.</Alert>}
  <details className="analysis-advanced" hidden={!showForm&&!proposalMode}><summary>다른 비교 방식 선택</summary><div className="analytics-modes" role="group" aria-label="분석 유형">{modes.map(({id,title,icon:Icon})=><button key={id} type="button" aria-pressed={mode===id} className={mode===id?'active':''} disabled={proposalMode||submitting} onClick={()=>changeMode(id)}><Icon size={17}/>{title}</button>)}</div></details>
  {showForm&&<details className="analysis-advanced"><summary>측정 항목과 사용 가능한 기간</summary>{catalog.data&&<CatalogSummary catalog={catalog.data}/>}</details>}
  {restoring!==null&&<p className="analytics-help" role="status">선택한 분석의 최신 조건을 불러오는 중입니다.</p>}
  {(!proposalMode||reviewedProposal?.request)&&<form ref={formRef} className="analytics-form" hidden={!showForm&&!proposalMode} onChangeCapture={()=>restoreGuard.current.invalidate()} onClickCapture={()=>restoreGuard.current.invalidate()} onSubmit={event=>{event.preventDefault();if(restoring===null)void submit()}}>
   <fieldset disabled={restoring!==null||submitting||proposalMode&&latestProposal?.status!=='pending'} style={{border:0,padding:0,margin:0,minWidth:0}}>
   <p className="analysis-scope">분석 범위: {scopeProject?.name??projects.find(p=>p.id===requestProject)?.name??(requestProject===null?'전체 프로젝트':`프로젝트 ${requestProject}`)}</p>{!proposalMode&&<TextField select size="small" label="분석할 프로젝트" value={requestProject??'all'} onChange={e=>{setRequestProject(e.target.value==='all'?null:Number(e.target.value));setScopeProject(undefined);setReferences([]);setDefinitions([])}}><MenuItem value="all">전체 프로젝트</MenuItem>{projects.map(p=><MenuItem value={p.id} key={p.id}>{p.name}</MenuItem>)}</TextField>}
   <div className="analytics-section-heading"><div><h2>{mode==='split'?analysisPaths[focus].question:currentMode.title}</h2><p>{mode==='split'?'공정과 두 Recipe, 측정 기간을 확인해 주세요.':currentMode.description}</p></div><span className="analytics-timezone">입력 시각: {Intl.DateTimeFormat().resolvedOptions().timeZone}</span></div>
   {fixedTargetLabel&&<Alert severity="info"><strong>이 조건안의 분석 대상: {fixedTargetLabel}</strong><p>이 대상 범위를 유지하여 분석합니다. 다른 대상은 채팅에서 새 조건안을 요청해 주세요.</p></Alert>}
   {purpose==='validation'&&<p className="analytics-help"><strong>구현 검증용 조건</strong>을 불러왔습니다. 운영 기준으로 승인된 값이 아니며, 다시 실행한 결과도 구현 검증으로 구분합니다.</p>}
   {mode==='trend'&&<Autocomplete className="analytics-series-picker" options={catalog.data?.series.filter(s=>s.family===focus)??[]} value={series} onChange={(_,value)=>setSeries(value)} isOptionEqualToValue={(a,b)=>seriesKey(a)===seriesKey(b)} getOptionLabel={seriesName} noOptionsText="일치하는 PARA가 없습니다" renderInput={params=><TextField {...params} label="분석 PARA" placeholder="공정 또는 PARA 이름 검색"/>}/>}
   {mode==='split'&&<>
    <div className="analytics-fields three"><TextField select label="변경·평가한 공정" value={operation} onChange={e=>{setOperation(e.target.value);setRecipeA('');setRecipeB('')}}><MenuItem value="">선택해 주세요</MenuItem>{catalog.data?.recipe_operations.map(item=><MenuItem key={item.operation} value={item.operation}>{item.operation}</MenuItem>)}</TextField><TextField select label="기준 Recipe" value={recipeA} disabled={!operation} onChange={e=>setRecipeA(e.target.value)}><MenuItem value="">선택해 주세요</MenuItem>{recipes.map(recipe=><MenuItem key={recipe} value={recipe}>{recipe}</MenuItem>)}</TextField><TextField select label="평가 Recipe" value={recipeB} disabled={!operation} onChange={e=>setRecipeB(e.target.value)}><MenuItem value="">선택해 주세요</MenuItem>{recipes.map(recipe=><MenuItem key={recipe} value={recipe}>{recipe}</MenuItem>)}</TextField></div>
    <p className="analytics-help">선택한 recipe가 기록된 wafer를 비교합니다. 관찰된 그룹 간 차이이며, recipe 변경의 인과 효과로 확정하지 않습니다.</p>
    <details className="analytics-optional"><summary>비교할 lot 한정 (선택)</summary><div className="analytics-fields"><TextField label="그룹 A의 lot ID" multiline minRows={2} value={lotsA} onChange={e=>setLotsA(e.target.value)} helperText="쉼표 또는 줄바꿈으로 구분 · 비우면 해당 recipe의 전체 lot"/><TextField label="그룹 B의 lot ID" multiline minRows={2} value={lotsB} onChange={e=>setLotsB(e.target.value)} helperText="쉼표 또는 줄바꿈으로 구분 · 비우면 해당 recipe의 전체 lot"/></div></details>
   </>}
   {(mode==='statistics'||mode==='split')?<div className="analytics-period"><span>{mode==='statistics'?'조회 기간 · 비우면 전체 이력':'측정값 비교 기간'}</span><div className="analytics-fields"><DateInput label="시작 시각" value={start} change={setStart}/><DateInput label="종료 시각 (미포함)" value={end} change={setEnd}/></div></div>:<div className="analytics-periods"><div className="analytics-period"><span>기준 기간</span><div className="analytics-fields"><DateInput label="기준 시작" value={baselineStart} change={setBaselineStart}/><DateInput label="기준 종료 (미포함)" value={baselineEnd} change={setBaselineEnd}/></div></div><div className="analytics-period"><span>비교 기간</span><div className="analytics-fields"><DateInput label="비교 시작" value={currentStart} change={setCurrentStart}/><DateInput label="비교 종료 (미포함)" value={currentEnd} change={setCurrentEnd}/></div></div></div>}
   {mode!=='statistics'&&<>
    <div className="analytics-fields three analytics-criteria"><TextField select label="반복 측정 처리" value={retest} onChange={e=>setRetest(e.target.value as RetestPolicy|'')}><MenuItem value="">기준 확인 필요</MenuItem><MenuItem value="latest_per_site">동일 측정 위치의 마지막 값</MenuItem><MenuItem value="all_events">모든 측정 이력</MenuItem></TextField><details className="analysis-advanced"><summary>변화 판정 기준 (선택)</summary><TextField label={`${mode==='trend'?'':'공통 '}최소 유효 표본 수 (선택)`} type="number" value={minimum} onChange={e=>setMinimum(e.target.value)} inputProps={{min:1,max:1000000,step:1}}/><TextField label={`${mode==='trend'?'':'공통 '}평균 변화량 절댓값 기준 (선택)`} type="number" value={delta} onChange={e=>setDelta(e.target.value)} inputProps={{min:0,step:'any'}}/></details></div>
    <p className="analytics-help">기간과 반복 측정 기준이 미정이면 확인 요청에서 먼저 확인합니다. 판정 기준이 없으면 수치 차이를 계산하고 판정은 보류합니다.{mode!=='trend'&&' 여러 PARA의 단위가 다르면 아래에서 PARA별 기준을 지정하세요.'}</p>
    {(mode==='changes'||mode==='split')&&<SeriesCriteriaEditor rows={seriesCriteria} options={catalog.data?.series??[]} onChange={setSeriesCriteria} onAdd={()=>setSeriesCriteria(value=>[...value,{id:nextCriterionId.current++,series:null,minimum:'',delta:''}])}/>}
   </>}
   {!!definitions.length&&<div className="analytics-approved"><h3>확인 요청에서 승인된 기준</h3><p className="analytics-help">승인된 설명을 확인하고 위의 기간·처리 방식·판정값을 직접 입력하세요. 연결한 지식의 버전을 분석 근거로 함께 보관합니다.</p>{definitions.map(definition=><div key={`${definition.item_id}:${definition.version}`}><ApprovedDefinition definition={definition}/><FormControlLabel control={<Checkbox size="small" checked={references.some(ref=>ref.item_id===definition.item_id&&ref.version===definition.version)} onChange={(_,checked)=>setReferences(value=>checked?[...value.filter(ref=>ref.item_id!==definition.item_id),{item_id:definition.item_id,version:definition.version}]:value.filter(ref=>ref.item_id!==definition.item_id))}/>} label="승인 지식을 근거로 연결"/></div>)}</div>}
   {!!references.length&&!definitions.length&&<p className="analytics-help">이전에 연결한 승인 지식 {references.length}개를 근거로 유지합니다. <button type="button" className="analytics-row-expand" onClick={()=>setReferences([])}>연결 해제</button></p>}
   <ErrorNotice error={error}/>
   <div className="analytics-form-actions"><Button type="submit" variant="contained" startIcon={missingRequired?<Inbox size={16}/>:<Play size={16}/>} disabled={!canRun||!catalog.data?.supported||busy||proposalMode&&!proposalReady||(mode==='trend'&&!series)}>{submitting?'요청 중':busy?'분석 진행 중':missingRequired?(proposalMode?'확인하고 기준 질문 등록':'기준 확인 요청'):proposalMode?(remainingBlockers.length?'조건 확인 후 진행':'확인한 조건으로 분석 실행'):'분석 실행'}</Button>{mode==='trend'&&!series&&<span>분석할 PARA를 먼저 선택해 주세요.</span>}{!canRun&&<span>분석 실행은 편집 권한이 있는 사용자에게 제공됩니다.</span>}<span>결과와 연결된 질문은 분석 기록에 남습니다.</span></div>
   {proposalMode&&!missingRequired&&mode!=='statistics'&&(!minimum||!delta)&&<p className="analytics-help">판정 기준이 없는 PARA는 수치만 계산하고 변화 판정은 보류합니다.</p>}
   </fieldset>
  </form>}
  {!proposalMode&&(selected||showForm)&&<div className="analytics-results-layout focused-results"><aside hidden className="analytics-history"><div className="analytics-section-heading"><h2>분석 이력</h2><span>{history.data?.length??0}건</span></div>{history.isPending?<Loading/>:!history.data?.length?<p className="analytics-help">실행한 분석이 여기에 저장됩니다.</p>:<div className="analytics-history-list">{history.data.map(run=><button type="button" key={run.id} className={selected===run.id?'selected':''} aria-pressed={selected===run.id} onClick={()=>setSelected(run.id)}><span><strong>{runName(run)}</strong><RunStatus status={run.status}/></span><small>{run.request.parameter??(run.kind==='split'?run.request.operation??'공정 미정':'전체 PARA')}</small><time dateTime={run.created_at}>{formatDate(run.created_at)}</time></button>)}</div>}</aside>
   <section className="analytics-result" aria-live="polite"><ErrorNotice error={detail.error}/>{selected&&detail.isPending&&!active?<Loading/>:active?<RunResult run={active} focus={focus} canSchedule={canRun&&visible} workspace={workspace} onOpenRun={setSelected} onRestore={()=>void restoreCurrent()} onOpenKnowledge={onOpenKnowledge}/>:<div className="analytics-empty"><ChartNoAxesCombined size={30}/><h3>분석 결과를 확인하세요</h3><p>위에서 비교를 시작하거나 분석 기록에서 저장된 결과를 열어 보세요.</p></div>}</section>
  </div>}
 </section>;
}

function DateInput({label,value,change}:{label:string;value:string;change:(value:string)=>void}){return <TextField label={label} type="datetime-local" value={value} onChange={e=>change(e.target.value)} inputProps={{step:'any'}} InputLabelProps={{shrink:true}}/>}
function SeriesCriteriaEditor({rows,options,onChange,onAdd}:{rows:SeriesCriterionInput[];options:AnalysisSeries[];onChange:(rows:SeriesCriterionInput[])=>void;onAdd:()=>void}){
 function update(id:number,patch:Partial<SeriesCriterionInput>){onChange(rows.map(row=>row.id===id?{...row,...patch}:row))}
 return <details className="analytics-optional analytics-series-criteria"><summary>PARA별 판정 기준 (선택){rows.length>0&&` · ${rows.length}개`}</summary><p className="analytics-help">PARA별 기준을 지정하면 해당 PARA에는 공통 기준보다 우선 적용합니다. 나머지 PARA는 공통 기준을 사용하며, 적용할 기준이 없으면 확인 요청에서 확인합니다.</p>
  <div className="analytics-series-criteria-list">{rows.map((row,index)=><div key={row.id} className="analytics-series-criterion" role="group" aria-label={`${index+1}번째 PARA별 판정 기준`}>
   <Autocomplete options={options.filter(option=>!rows.some(other=>other.id!==row.id&&other.series&&seriesKey(other.series)===seriesKey(option)))} value={row.series} onChange={(_,value)=>update(row.id,{series:value})} isOptionEqualToValue={(a,b)=>seriesKey(a)===seriesKey(b)} getOptionLabel={seriesName} noOptionsText="추가할 PARA가 없습니다" renderInput={params=><TextField {...params} label="기준을 적용할 PARA" placeholder="공정 또는 PARA 검색" size="small"/>}/>
   <TextField label="최소 유효 표본 수" type="number" value={row.minimum} onChange={e=>update(row.id,{minimum:e.target.value})} inputProps={{min:1,max:1000000,step:1}} size="small"/>
   <TextField label="평균 변화량 절댓값" type="number" value={row.delta} onChange={e=>update(row.id,{delta:e.target.value})} inputProps={{min:0,step:'any'}} size="small"/>
   <IconButton size="small" aria-label={`${row.series?seriesName(row.series):`${index+1}번째 PARA`} 판정 기준 삭제`} onClick={()=>onChange(rows.filter(item=>item.id!==row.id))}><Trash2 size={16}/></IconButton>
  </div>)}</div><Button type="button" size="small" startIcon={<Plus size={15}/>} disabled={rows.length>=Math.min(options.length,5000)} onClick={onAdd}>PARA별 기준 추가</Button>
 </details>;
}
function CatalogSummary({catalog}:{catalog:AnalysisCatalog}){return <div className="analytics-catalog"><div className="analytics-source-tags">{catalog.sources.map(source=><span key={source.source_id} className={!source.available?'unavailable':''}>{familyName(source.family)} 측정{!source.available&&' · 사용 불가'}</span>)}</div><div className="analytics-catalog-meta"><span><b>{catalog.series.length.toLocaleString()}</b>개 측정 계열</span>{catalog.extent&&<span>{formatDate(catalog.extent.start)} ~ {formatDate(catalog.extent.end)}</span>}<span>Inline은 공정과 측정 항목별로 구분</span></div>{catalog.issues.length>0&&<ul className="analytics-notes">{catalog.issues.map((issue,i)=><li key={i}>{issue}</li>)}</ul>}</div>}
function RunStatus({status}:{status:AnalysisRun['status']}){return <span className={`analytics-status ${status}`}>{statuses[status]}</span>}
function ApprovedDefinition({definition}:{definition:ApprovedAnalysisDefinition}){
 const labels=Object.fromEntries(Object.values(detailFields).flat()),content=definition.content;
 return <article className="analytics-approved-definition"><strong>{content.name||'승인된 정의'} <small>v{definition.version} · 승인됨</small></strong>{content.description&&<p>{content.description}</p>}{Object.entries(content.details??{}).filter(([,value])=>typeof value==='string'&&value).map(([key,value])=><p key={key}><b>{labels[key]??key}</b> {String(value)}</p>)}</article>;
}
function RunResult({run,focus,workspace,canSchedule,onOpenRun,onRestore,onOpenKnowledge}:{run:AnalysisRun;focus:AnalysisFocus;canSchedule:boolean;workspace:string;onOpenRun:(id:string)=>void;onRestore:()=>void;onOpenKnowledge?:(run?:AnalysisRun,questionId?:string)=>void}){
 const result=run.result;
 const [display,setDisplay]=useState<AnalysisFocus|'all'>(focus);
 useEffect(()=>{setDisplay(focus)},[run.id,focus]);
 return <>
  <div className="analytics-section-heading"><div><h2>{resultTitle(run)}</h2><p><Clock3 size={13}/> {formatDate(run.completed_at??run.created_at)} <RunStatus status={run.status}/></p></div><Button size="small" onClick={onRestore}>조건 불러오기</Button></div>
  <p className="analysis-scope">{run.request._project_scope?.name??(runProject(run)===null?'공통 · 전체 프로젝트':`프로젝트 ${runProject(run)}`)}</p><details><summary>사용한 조건과 데이터 시각</summary><RunScope run={run}/></details>
  {run.schedule_id&&<Alert severity="info">정기 분석에서 실행한 작업입니다. 취소·실패·기준 확인 시 예약이 일시정지됩니다. 재시도는 이 실행의 기간을 그대로 사용합니다.</Alert>}
  {canSchedule&&<ScheduleAnalysisButton key={`schedule:${workspace}:${run.id}`} run={run} workspace={workspace}/>}
  <JobActionButtons key={`${workspace}:${run.id}`} target={{kind:'analysis_run',id:run.id,project_id:run.request.project_id??null}} controls={run.controls} workspace={workspace} onOpenRun={onOpenRun}/>
  {run.retry_of&&<Button size="small" onClick={()=>onOpenRun(run.retry_of!)}>원래 작업 열기</Button>}
  {run.status==='cancelled'&&<Alert severity="info">취소가 완료되었습니다. 기존 질문·답변·승인 내용은 유지됩니다.</Alert>}
  {busyRun(run)&&<Loading label={run.status==='cancelling'?'취소 요청을 확인 중입니다. 새 결과는 반영하지 않습니다.':'전체 대상 데이터를 분석하고 있습니다'}/>}
  {run.error&&<ErrorNotice error={new Error(run.error)}/>}
  {!!run.questions?.length&&<div className="analytics-questions"><strong>분석 기준 확인</strong><ul>{run.questions.map(question=><li key={question.id}>{question.title}{onOpenKnowledge&&<Button size="small" onClick={()=>onOpenKnowledge(run,question.id)}>이 질문 확인</Button>}{question.approved_definition&&<ApprovedDefinition definition={question.approved_definition}/>}</li>)}</ul><div className="analytics-inline-actions">{onOpenKnowledge&&<Button size="small" endIcon={<ArrowRight size={14}/>} onClick={()=>onOpenKnowledge()}>연결된 확인 요청 열기</Button>}{run.questions.some(question=>question.approved_definition)&&<Button size="small" onClick={onRestore}>승인 기준을 확인하며 조건 입력</Button>}</div></div>}
  {run.status==='needs_input'&&!run.questions?.length&&<p className="analytics-help">분석 조건 확인이 필요합니다. 조건을 불러와 미정인 항목을 지정한 뒤 다시 실행해 주세요.</p>}
  {result&&<>
   <AnalysisBrief run={run} focus={focus}/>
   <div className="analytics-result-summary"><span><b>{result.series.length.toLocaleString()}</b>개 측정 계열</span>{result.row_count!=null&&<span><b>{result.row_count.toLocaleString()}</b>개 분석 행</span>}{result.series.some(item=>item.status==='changed')&&<span><b>{result.series.filter(item=>item.status==='changed').length}</b>개 변화 감지</span>}</div>
   {run.kind==='split'&&<div className="analytics-split-coverage"><span>recipe 매칭 {formatNumber(result.matched_count)}</span><span>이력 미매칭 {formatNumber(result.unmatched_count)}</span><span>모호한 wafer 제외 {formatNumber(result.excluded_ambiguous_wafer_count)}</span><span>모호한 측정 제외 {formatNumber(result.excluded_ambiguous_measurement_count)}</span>{!!result.overlapping_lots?.length&&<span>양쪽 그룹에 포함된 lot {result.overlapping_lots.length}개</span>}</div>}
   {!!result.warnings?.length&&<details><summary>비교 범위와 해석 시 유의할 점</summary><ul className="analytics-notes">{result.warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul></details>}
   {run.kind==='trend'&&result.series.length===1&&!!result.series[0].points?.length&&<TrendChart series={result.series[0]} baseline={result.windows?.baseline} current={result.windows?.current}/>}
   <div className="analysis-result-filter"><strong>상세 비교</strong>{(['epm','inline','all'] as const).map(value=><Button key={value} aria-pressed={display===value} variant={display===value?'contained':'text'} onClick={()=>setDisplay(value)}>{value==='all'?'전체 측정':value.toUpperCase()}</Button>)}</div><ResultTable key={`${run.id}:${display}`} run={run} family={display}/>
  </>}
 </>;
}
function RunScope({run}:{run:AnalysisRun}){
 const request=run.request,result=run.result;
 return <dl className="analytics-scope">
  {request._project_scope&&<div><dt>프로젝트 범위</dt><dd>{request._project_scope.name} · {request._project_scope.lot_cds.join(', ')}</dd></div>}
  {request.purpose==='validation'&&<div><dt>실행 목적</dt><dd>구현 검증 · 입력된 기간과 판정값은 검증용이며 승인된 운영 기준이 아닙니다.</dd></div>}
  {run.kind==='trend'?<><div><dt>기준 기간</dt><dd>{formatDate(request.baseline_start)} ~ {formatDate(request.baseline_end)}</dd></div><div><dt>비교 기간</dt><dd>{formatDate(request.current_start)} ~ {formatDate(request.current_end)}</dd></div></>:<div><dt>조회 기간</dt><dd>{!request.start&&!request.end&&run.kind==='statistics'?'전체 이력':`${formatDate(request.start)} ~ ${formatDate(request.end)}`}</dd></div>}
  {run.kind==='split'&&<div><dt>비교 그룹</dt><dd>{request.operation??'공정 미정'} · A: {request.recipe_a??'미정'} / B: {request.recipe_b??'미정'}{request.lot_ids_a?.length?` · A ${request.lot_ids_a.length} lot`:''}{request.lot_ids_b?.length?` · B ${request.lot_ids_b.length} lot`:''}</dd></div>}
  {run.kind==='statistics'?<div><dt>집계 대상</dt><dd>모든 측정 이력 · 결측 포함 건수와 유효값 통계를 구분</dd></div>:<><div><dt>반복 측정</dt><dd>{request.retest_policy==='latest_per_site'?'동일 위치의 마지막 값':request.retest_policy==='all_events'?'모든 이력':'확인 필요'}</dd></div><div><dt>{request.series_criteria?.length?'공통 판정 기준':'변화 판정'}</dt><dd>최소 표본 {request.min_samples??'미정'} · 평균 차이 절댓값 {request.absolute_delta??'미정'}</dd></div>{!!request.series_criteria?.length&&<div><dt>PARA별 기준</dt><dd>{request.series_criteria.length}개 · 각 PARA의 상세 결과에서 적용값 확인</dd></div>}</>}
  {run.source_watermark&&<><div><dt>데이터 확인 시각</dt><dd>{formatDate(run.source_watermark.captured_at)}</dd></div>{Object.entries(run.source_watermark.sources??{}).map(([family,source])=><div key={family}><dt>{family==='history'?'Recipe':familyName(family)} 원천</dt><dd>{formatNumber(source.row_count)}행 · 마지막 측정 {formatDate(source.last_measured_at)}</dd></div>)}</>}
  {!!request.knowledge_refs?.length&&<div><dt>승인 지식 근거</dt><dd>{request.knowledge_refs.length}개 정의의 지정 버전</dd></div>}
  {result?.bucket==='day'&&<div><dt>추세 집계</dt><dd>일별 · UTC 날짜 기준</dd></div>}
 </dl>;
}
function ResultTable({run,family}:{run:AnalysisRun;family:AnalysisFocus|'all'}){
 const [search,setSearch]=useState(''),[page,setPage]=useState(0),[filter,setFilter]=useState('all'),[expanded,setExpanded]=useState<string|null>(null);
 const rows=useMemo(()=>(run.result?.series??[]).filter(item=>family==='all'||item.family===family).filter(item=>seriesName(item).toLocaleLowerCase().includes(search.toLocaleLowerCase())&&(filter==='all'||item.status===filter)),[run.result,search,filter,family]);
 const pageSize=40,pageCount=Math.max(1,Math.ceil(rows.length/pageSize)),currentPage=Math.min(page,pageCount-1),visible=rows.slice(currentPage*pageSize,(currentPage+1)*pageSize),stats=run.kind==='statistics';
 return <><div className="analytics-table-toolbar"><TextField size="small" label="공정·PARA 검색" value={search} onChange={e=>{setSearch(e.target.value);setPage(0)}}/>{!stats&&<TextField select size="small" label="판정" value={filter} onChange={e=>{setFilter(e.target.value);setPage(0)}}><MenuItem value="all">모든 판정</MenuItem>{Object.entries(resultStatuses).map(([key,label])=><MenuItem key={key} value={key}>{label}</MenuItem>)}</TextField>}<span>{rows.length.toLocaleString()}개</span></div>
  <div className="analytics-table-scroll"><table className="analytics-table"><caption>{stats?'PARA별 기본 통계':run.kind==='split'?'recipe 그룹별 평균 차이 · 차이는 B − A':'기간별 평균 차이 · 차이는 비교 − 기준'}</caption><thead><tr><th scope="col">측정 계열</th>{stats?<><th scope="col">유효 / 전체</th><th scope="col">결측</th><th scope="col">평균</th><th scope="col">중앙값</th><th scope="col">표준편차</th><th scope="col">최솟값</th><th scope="col">최댓값</th></>:<><th scope="col">{run.kind==='split'?'A 평균':'기준 평균'}</th><th scope="col">{run.kind==='split'?'B 평균':'비교 평균'}</th><th scope="col">차이</th><th scope="col">변화율</th><th scope="col">유효 표본<br/>{run.kind==='split'?'A / B':'기준 / 비교'}</th><th scope="col">판정</th></>}<th scope="col">상세</th></tr></thead><tbody>{visible.map(item=>{
   const a=item.baseline??item.group_a,b=item.current??item.group_b,s=item.statistics,key=seriesKey(item);
   return <AnalysisTableRows key={key} item={item} stats={stats} a={a} b={b} s={s} expanded={expanded===key} toggle={()=>setExpanded(expanded===key?null:key)} kind={run.kind}/>;
  })}</tbody></table>{!visible.length&&<p className="analytics-table-empty">조건에 맞는 측정 계열이 없습니다.</p>}</div>
  {pageCount>1&&<div className="analytics-pagination"><Button size="small" aria-label="이전 결과 페이지" disabled={currentPage===0} onClick={()=>setPage(currentPage-1)} startIcon={<ChevronLeft size={15}/>}>이전</Button><span>{currentPage+1} / {pageCount}</span><Button size="small" aria-label="다음 결과 페이지" disabled={currentPage+1>=pageCount} onClick={()=>setPage(currentPage+1)} endIcon={<ChevronRight size={15}/>}>다음</Button></div>}
 </>;
}
function AnalysisTableRows({item,stats,a,b,s,expanded,toggle,kind}:{item:AnalysisResultSeries;stats:boolean;a?:AnalysisStatistics;b?:AnalysisStatistics;s?:AnalysisStatistics;expanded:boolean;toggle:()=>void;kind:AnalysisRun['kind']}){
 return <>
  <tr><th scope="row"><span className="analytics-family-tag">{familyName(item.family)}</span>{item.operation&&<small>{item.operation}</small>}<strong>{item.parameter}</strong></th>
   {stats?<><td>{formatNumber(s?.valid_count)} / {formatNumber(s?.event_count)}</td><td>{formatNumber(s?.missing_count)}</td><td>{formatNumber(s?.mean)}</td><td>{formatNumber(s?.median)}</td><td>{formatNumber(s?.stddev)}</td><td>{formatNumber(s?.min)}</td><td>{formatNumber(s?.max)}</td></>:<><td>{formatNumber(a?.mean)}</td><td>{formatNumber(b?.mean)}</td><td>{item.delta!=null&&item.delta>0?'+':''}{formatNumber(item.delta)}</td><td>{item.percent_change==null?'—':`${formatNumber(item.percent_change)}%`}</td><td>{formatNumber(a?.valid_count)} / {formatNumber(b?.valid_count)}</td><td><span className={`analytics-detection ${item.status??''}`}>{item.status?resultStatuses[item.status]:'—'}</span></td></>}
   <td><button type="button" className="analytics-row-expand" aria-expanded={expanded} aria-label={`${seriesName(item)} 상세 ${expanded?'접기':'보기'}`} onClick={toggle}>{expanded?'접기':'보기'}</button></td>
  </tr>
  {expanded&&<tr className="analytics-expanded-row"><td colSpan={stats?9:8}>{item.criteria&&<p className="analytics-effective-criteria"><strong>적용 판정 기준</strong> 최소 유효 표본 수 {item.criteria.min_samples??'미정'} · 평균 변화량 절댓값 {item.criteria.absolute_delta??'미정'}</p>}<div className="analytics-comparison-detail">{stats?<StatisticsSummary title="측정 현황과 분포" stats={s}/>:<><StatisticsSummary title={kind==='split'?'그룹 A':'기준 기간'} stats={a}/><StatisticsSummary title={kind==='split'?'그룹 B':'비교 기간'} stats={b}/></>}</div>{kind==='trend'&&!!item.points?.length&&<TrendChart series={item}/>}</td></tr>}
 </>;
}
function StatisticsSummary({title,stats}:{title:string;stats?:AnalysisStatistics}){return <div><strong>{title}</strong><dl><div><dt>lot / wafer</dt><dd>{formatNumber(stats?.lot_count)} / {formatNumber(stats?.wafer_count)}</dd></div><div><dt>결측 / 전체 측정</dt><dd>{formatNumber(stats?.missing_count)} / {formatNumber(stats?.event_count)}</dd></div><div><dt>중앙값 / 표준편차</dt><dd>{formatNumber(stats?.median)} / {formatNumber(stats?.stddev)}</dd></div><div><dt>최솟값 ~ 최댓값</dt><dd>{formatNumber(stats?.min)} ~ {formatNumber(stats?.max)}</dd></div><div><dt>25% ~ 75% 분위수</dt><dd>{formatNumber(stats?.q25)} ~ {formatNumber(stats?.q75)}</dd></div></dl></div>}
function TrendChart({series,baseline,current}:{series:AnalysisResultSeries;baseline?:{start:string;end:string};current?:{start:string;end:string}}){
 const points=(series.points??[]).filter(point=>Number.isFinite(Date.parse(point.at))),values=points.filter((point):point is AnalysisPoint&{mean:number}=>point.mean!=null&&Number.isFinite(point.mean));
 if(!values.length)return <p className="analytics-help">일별 추세를 그릴 유효한 측정값이 없습니다.</p>;
 const width=760,height=220,left=70,right=18,top=18,bottom=36,times=points.map(point=>Date.parse(point.at)),low=Math.min(...values.map(point=>point.mean)),high=Math.max(...values.map(point=>point.mean)),padding=(high-low)*.1||Math.abs(high)*.05||1,min=low-padding,max=high+padding,first=Math.min(...times),last=Math.max(...times),timeRange=last-first||86400000;
 const x=(time:number)=>left+(time-first)/timeRange*(width-left-right),y=(value:number)=>top+(max-value)/(max-min)*(height-top-bottom);
 let path='',previous:number|null=null;
 for(const point of points){const time=Date.parse(point.at);if(point.mean==null){previous=null;continue}path+=`${previous==null||time-previous>86400000*1.5?' M':' L'}${x(time)},${y(point.mean)}`;previous=time}
 return <figure className="analytics-chart"><figcaption><strong>{seriesName(series)}</strong><span>일별 평균 · UTC 날짜 기준</span></figcaption><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${seriesName(series)} 일별 평균 추세. 최솟값 ${formatNumber(low)}, 최댓값 ${formatNumber(high)}.`}>
  {[0,.5,1].map(ratio=>{const value=min+(max-min)*ratio;return <g key={ratio}><line x1={left} x2={width-right} y1={y(value)} y2={y(value)} stroke="#e4ebed"/><text x={left-8} y={y(value)+4} textAnchor="end">{formatNumber(value)}</text></g>})}
  <path d={path} fill="none" stroke="#267c86" strokeWidth="2"/>{values.map(point=><circle key={point.at} cx={x(Date.parse(point.at))} cy={y(point.mean)} r={values.length>120?1.5:3} fill="#267c86"><title>{point.at.slice(0,10)} · 평균 {formatNumber(point.mean)} · 유효 표본 {point.valid_count}</title></circle>)}
  <text x={left} y={height-9}>{new Date(first).toISOString().slice(0,10)}</text>{last!==first&&<text x={width-right} y={height-9} textAnchor="end">{new Date(last).toISOString().slice(0,10)}</text>}
 </svg>{baseline&&current&&<p>기준 {shortDate(baseline.start)}–{shortDate(baseline.end)} · 비교 {shortDate(current.start)}–{shortDate(current.end)}</p>}<details><summary>일별 수치 보기 ({points.length}일)</summary><div className="analytics-table-scroll"><table className="analytics-table"><thead><tr><th scope="col">일자 (UTC)</th><th scope="col">평균</th><th scope="col">유효 표본</th><th scope="col">결측</th></tr></thead><tbody>{points.map(point=><tr key={point.at}><td>{point.at.slice(0,10)}</td><td>{formatNumber(point.mean)}</td><td>{formatNumber(point.valid_count)}</td><td>{formatNumber(point.missing_count)}</td></tr>)}</tbody></table></div></details></figure>;
}
