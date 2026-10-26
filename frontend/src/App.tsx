import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Button,Drawer,Fab,IconButton,Menu,MenuItem,Snackbar,Tooltip} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ArrowRight,BookOpen,ChevronDown,KeyRound,Layers3,LayoutDashboard,Settings2,ShieldCheck,Sparkles,Table2,X} from 'lucide-react';
import {api,setWorkspaceToken} from './api';
import {databaseStatusSignal} from './databaseStatus';
import {analysisJourneyCurrent,analysisReturnValid,selectedAnalysisRun,type AnalysisKnowledgeJourney,type AnalysisResume,type AnalysisSelection} from './analysisSelection';
import {loadAnalysisRun} from './analyticsApi';
import type {AnalysisRun,ApprovedAnalysisDefinition} from './analyticsTypes';
import type {AnalysisProposalView} from './analysisProposal.generated';
import type {AnalysisProposalSelection} from './analysisProposalState';
import type {AuthSession,Bootstrap,CardSpec,DatabaseStatus,ISOWeek,LlmStatus,Proposal,QueryContext} from './types';
import Dashboard from './Dashboard';
import {loadPeriod} from './dashboardPeriod';
import DataManager from './DataManager';
import ChatDrawer from './ChatDrawer';
import {chatSelectedDataset} from './chatDataAnalysis';
import AssistantProposalDialog from './AssistantProposalDialog';
import LlmSettingsDialog from './LlmSettingsDialog';
import Admin from './Admin';
import SemanticWorkspace from './SemanticWorkspace';
import AnalyticsWorkspace from './AnalyticsWorkspace';
import WorkspaceActivity from './WorkspaceActivityView';
import type {AnalysisFocus,AnalysisStart} from './analysisExperience';
import './analysis-experience.css';
import type {OperationAction,OperationJump} from './operationsState';
import {LocalDraftProvider} from './localDraft';
import './workspace-navigation.css';
import {ConnectionSignal} from './DatabaseSettings';
import {ErrorNotice,Loading} from './components';
function Brand({light=false}:{light?:boolean}){return <div className={`brand ${light?'brand-light':''}`}><div className="brand-mark"><span/><span/><span/><span/></div><span>Project<span className="brand-name-second"> Manager</span><small>TEAM INTELLIGENCE WORKSPACE</small></span></div>}
function AuthGate({session,error,pending,onRefresh}:{session?:AuthSession;error:unknown;pending:boolean;onRefresh:()=>void}){
 const title=error?'서버에 연결할 수 없습니다':session?.status==='disabled'?'접근 권한을 확인해 주세요':session?.status==='unavailable'?'사내 SSO 연결을 확인해 주세요':'사내 SSO로 로그인해 주세요';
 return <div className="login-page">
  <section className="login-story"><Brand light/><div className="login-story-content"><span className="overline">FROM WAFER TO INSIGHT</span><h1>Every wafer.<br/>A clearer picture.</h1><p>흩어져 있는 프로젝트 데이터를 연결하고,<br/>다음 공정의 가능성을 발견하세요.</p><div className="login-wafer"><div/><div/><div/><div/><div/><div/><div/><div/><div/></div></div><div className="login-footer">PROJECT ANALYTICS <span>·</span> BUILT FOR YOUR TEAM</div></section>
  <section className="login-form-section"><div className="login-form"><span className="overline">WELCOME TO PROJECT MANAGER</span><h2>{title}</h2><p>{session?.mode==='local_admin'?'로컬 테스트 관리자 세션을 확인합니다.':'사내 로그인 세션으로 팀 워크스페이스에 접속합니다.'}</p>
   <ErrorNotice error={error}/>{session?.message&&<Alert severity={session.status==='disabled'?'warning':'info'} sx={{mb:3}}>{session.message}</Alert>}
   <Button variant="contained" fullWidth onClick={onRefresh} disabled={pending} endIcon={<ArrowRight size={17}/>}>{pending?'확인 중…':'로그인 상태 다시 확인'}</Button>
   <div className="login-helper"><ShieldCheck size={15}/><span>{session?.status==='disabled'?'워크스페이스 관리자에게 계정 활성 상태를 문의하세요.':session?.status==='unavailable'?'관리자에게 사내 SSO 연결 설정을 문의하세요.':'사내 SSO에서 로그인한 후 다시 확인해 주세요.'}<br/>이름과 소속은 사내 디렉터리에서 확인합니다.</span></div>
  </div></section>
 </div>
}
export default function App(){
 const client=useQueryClient(),me=useQuery({queryKey:['auth-session'],queryFn:()=>api<AuthSession>('/auth/session'),retry:false});
 const currentUser=me.data?.user;
 const [llmSettings,setLlmSettings]=useState(false);
 const [analysisStart,setAnalysisStart]=useState<AnalysisStart|null>(null);
 const [analysisPanel,setAnalysisPanel]=useState<'analytics'|'activity'|null>(null);
 const [evaluationKnowledgeReturn,setEvaluationKnowledgeReturn]=useState(false);
 const [evaluationEntry,setEvaluationEntry]=useState<{id:string;projectId:number|null;token:number}|null>(null);
 function startAnalysis(focus:AnalysisFocus){setAnalysisProposalEntry(null);setAnalysisEntry(null);setJourney(null);setAnalysisResume(null);setAnalysisStart({focus,projectId:project,token:Date.now()});setAnalysisPanel('analytics')}
 const [adminTab,setAdminTab]=useState(0),[activityJump,setActivityJump]=useState<OperationJump|null>(null);
 function openOperation(action:OperationAction){
  if(action==='database'){setAdminTab(1);setAdmin(true)}
  else if(action==='llm_settings')setLlmSettings(true);
  else if(action==='knowledge'){setAnalysisPanel(null);setEvaluationKnowledgeReturn(false);setJourney(null);setKnowledgeEntry({area:'inbox',projectFilter:'all',token:Date.now()});setView('knowledge')}
  else {switchProject(null);setActivityJump({target:action,token:Date.now()});setAnalysisPanel('activity')}
 }
 const llmStatus=useQuery({queryKey:['llm-status',currentUser?.id],queryFn:()=>api<LlmStatus>('/llm/status'),enabled:!!currentUser,retry:false});
 useEffect(()=>{setLlmSettings(false)},[currentUser?.id]);
 const [workspace,setWorkspace]=useState(''),[workspaceReady,setWorkspaceReady]=useState(false),workspaceRef=useRef<string|null>(null);
 const dbStatus=useQuery({queryKey:['database-status'],queryFn:()=>api<DatabaseStatus>('/database-status'),enabled:!!currentUser,refetchInterval:30000,retry:false});
 const [project,setProject]=useState<number|null>(null),[view,setView]=useState<'dashboard'|'data'|'knowledge'>('dashboard'),[chat,setChat]=useState(false),[admin,setAdmin]=useState(false),[userMenu,setUserMenu]=useState<HTMLElement|null>(null),[toast,setToast]=useState(''),[selectedCard,setSelectedCard]=useState<CardSpec|null>(null),[datasetId,setDatasetId]=useState<string|null>(null),[proposalCard,setProposalCard]=useState<CardSpec|null>(null),[proposalDataset,setProposalDataset]=useState<Proposal['dataset']|null>(null);
 const [analysisEntry,setAnalysisEntry]=useState<AnalysisSelection|null>(null);
 const [analysisProposalEntry,setAnalysisProposalEntry]=useState<AnalysisProposalSelection|null>(null);
 const currentAnalysisProposal=analysisJourneyCurrent(analysisProposalEntry,workspace,currentUser?.id,currentUser?.role)?analysisProposalEntry:null;
 const analysisProposalRef=useRef(currentAnalysisProposal);analysisProposalRef.current=currentAnalysisProposal;
 function openAnalysisProposal(proposal:AnalysisProposalView){if(!currentUser||proposal.workspace_token!==workspace)return;setJourney(null);setAnalysisResume(null);setAnalysisProposalEntry({workspace,userId:currentUser.id,role:currentUser.role,proposal,token:Date.now()});setChat(false);setAnalysisPanel('analytics')}
 async function openAnalysisResult(id:string){
  if(!currentUser)return;
  try{
   const run=await api<{id:string;kind:string;project_id?:number|null}>(`/analytics/runs/${encodeURIComponent(id)}`);
   const actor=client.getQueryData<AuthSession>(['auth-session'])?.user;
   if(workspaceRef.current!==workspace||actor?.id!==currentUser.id||actor?.role!==currentUser.role)return;
   setAnalysisProposalEntry(null);setJourney(null);setAnalysisResume(null);
   if(run.kind==='evaluation'){setEvaluationEntry({id:run.id,projectId:run.project_id??null,token:Date.now()});setAnalysisPanel(null);setChat(true);return}
   setAnalysisEntry({workspace,userId:currentUser.id,runId:id});setChat(false);setAnalysisPanel('analytics');
  }catch(error){setToast((error as Error).message)}
 }
 function confirmedAnalysis(run:AnalysisRun,proposalId:string){if(analysisProposalRef.current?.proposal.proposal_id!==proposalId)return;setAnalysisProposalEntry(null);setAnalysisResume(null);rememberAnalysis(run.id)}
 const [journey,setJourney]=useState<AnalysisKnowledgeJourney|null>(null),[analysisResume,setAnalysisResume]=useState<AnalysisResume|null>(null),[returning,setReturning]=useState(false);
 const [visitedKnowledge,setVisitedKnowledge]=useState(false),[visitedAnalytics,setVisitedAnalytics]=useState(false);
 useEffect(()=>{if(view==='knowledge')setVisitedKnowledge(true);if(analysisPanel==='analytics')setVisitedAnalytics(true)},[view,analysisPanel]);
 useEffect(()=>{setAnalysisStart(null);setAnalysisEntry(null);setAnalysisProposalEntry(null);setJourney(null);setAnalysisResume(null);setReturning(false);setAnalysisPanel(null);setEvaluationKnowledgeReturn(false);setEvaluationEntry(null)},[workspace,currentUser?.id,currentUser?.role,currentUser?.active]);
 const [knowledgeEntry,setKnowledgeEntry]=useState<{area:'knowledge'|'inbox';projectFilter:string;questionId?:string|null;token?:number}>({area:'knowledge',projectFilter:'all'});
 const currentJourney=analysisJourneyCurrent(journey,workspace,currentUser?.id,currentUser?.role)?journey:null;
 const journeyRef=useRef(currentJourney);journeyRef.current=currentJourney;
 const rememberAnalysis=useCallback((id:string)=>{if(!currentUser)return;setAnalysisEntry(previous=>previous?.workspace===workspace&&previous.userId===currentUser.id&&previous.runId===id?previous:{workspace,userId:currentUser.id,runId:id})},[workspace,currentUser?.id]);
 function openAnalysisKnowledge(run?:AnalysisRun,questionId?:string){
  setEvaluationKnowledgeReturn(false);
  const question=run?.questions.find(q=>q.id===questionId);
  if(run&&question&&currentUser){
   rememberAnalysis(run.id);
   setJourney({workspace,userId:currentUser.id,role:currentUser.role,runId:run.id,projectId:run.request.project_id??run.request._project_scope?.id??null,questionId:question.id,questionTitle:question.title,definition:question.approved_definition?{item_id:question.approved_definition.item_id,version:question.approved_definition.version}:null});
  }else setJourney(null);
  setKnowledgeEntry({area:'inbox',projectFilter:'all',questionId:questionId??null,token:Date.now()});setAnalysisPanel(null);setView('knowledge');
 }
 const invalidateJourney=useCallback(()=>{setJourney(null);setToast('질문이나 승인 기준의 상태가 바뀌어 분석 연결을 해제했습니다. 분석에서 최신 질문을 다시 확인하세요.')},[]);
 const observeDefinition=useCallback((definition:ApprovedAnalysisDefinition)=>{setJourney(previous=>previous&&!previous.definition?{...previous,definition:{item_id:definition.item_id,version:definition.version}}:previous)},[]);
 async function returnToAnalysis(){
  const pending=journeyRef.current;if(!pending||returning)return;
  setReturning(true);
  try{
   const run=await loadAnalysisRun(pending.runId);
   if(journeyRef.current!==pending)return;
   if(!analysisReturnValid(pending,run)){invalidateJourney();return}
   rememberAnalysis(run.id);setAnalysisResume({workspace:pending.workspace,userId:pending.userId,role:pending.role,run,token:Date.now()});setJourney(null);setAnalysisPanel('analytics');
  }catch{if(journeyRef.current===pending)invalidateJourney()}finally{setReturning(false)}
 }
 const projectView=useRef<'dashboard'|'data'>('dashboard');
 useEffect(()=>{if(view==='dashboard'||view==='data')projectView.current=view},[view]);
 const bootstrapPath=`/bootstrap${project!==null?`?project_id=${project}`:''}`;
 const bootstrap=useQuery({queryKey:['bootstrap',project],queryFn:()=>api<Bootstrap>(bootstrapPath),enabled:!!currentUser&&workspaceReady});
 const reloadWorkspace=useCallback(async(token:string)=>{
  if(workspaceRef.current===token)return;
  workspaceRef.current=token;setWorkspaceReady(false);setWorkspaceToken(token);
  setProject(null);setSelectedCard(null);setDatasetId(null);setProposalCard(null);setProposalDataset(null);setChat(false);setAnalysisEntry(null);setWorkspace(token);
  const affected={predicate:(q:{queryKey:readonly unknown[]})=>q.queryKey[0]!=='auth-session'&&q.queryKey[0]!=='database-status'&&!(q.queryKey[0]==='admin'&&['databases','database-tables'].includes(String(q.queryKey[1])))};
  await client.cancelQueries({queryKey:['database-status']});
  client.setQueryData<DatabaseStatus>(['database-status'],old=>old?{...old,workspace_token:token}:old,{updatedAt:client.getQueryState(['database-status'])?.dataUpdatedAt});
  await client.cancelQueries(affected);
  client.removeQueries(affected);
  await client.fetchQuery({queryKey:['bootstrap',null],queryFn:()=>api<Bootstrap>('/bootstrap')}).catch(()=>undefined);
  setWorkspaceReady(true);
  void client.invalidateQueries({queryKey:['database-status']});
  void client.invalidateQueries({queryKey:['admin','databases']});
 },[client]);
 useEffect(()=>{if(dbStatus.data?.workspace_token)void reloadWorkspace(dbStatus.data.workspace_token);else if(dbStatus.error)setWorkspaceReady(true)},[dbStatus.data?.workspace_token,dbStatus.error,reloadWorkspace]);
 const [reviewProposal,setReviewProposal]=useState<Proposal|null>(null),[appliedProposals,setAppliedProposals]=useState<string[]>([]);
 useEffect(()=>{setReviewProposal(null);setAppliedProposals([])},[workspace,project,currentUser?.id,currentUser?.role]);
 const periodKey=`dashboard-period:${workspace}:${project??'common'}`;
 const [periodSelection,setPeriodSelection]=useState<{key:string;value:ISOWeek}|null>(null);
 const period=useMemo(()=>periodSelection?.key===periodKey?periodSelection.value:loadPeriod(periodKey),[periodKey,periodSelection]);
 function changePeriod(value:ISOWeek){setPeriodSelection({key:periodKey,value});try{sessionStorage.setItem(periodKey,JSON.stringify(value))}catch{}}
 const context=useMemo<QueryContext>(()=>({project_id:project,period_start:period}),[project,period]);
 const data=bootstrap.data,editable=!!currentUser&&currentUser.role!=='viewer';
 const projects=data?.projects??client.getQueryData<Bootstrap>(['bootstrap',null])?.projects??[],projectName=projects.find(p=>p.id===project)?.name;
 const selectedDatasetId=data?.datasets.some(d=>d.id===datasetId)?datasetId:null;
 function switchProject(id:number|null){setEvaluationEntry(null);setEvaluationKnowledgeReturn(false);setAnalysisPanel(null);setAnalysisProposalEntry(null);setJourney(null);setAnalysisResume(null);setProject(id);setSelectedCard(null);setDatasetId(null);setProposalCard(null);setProposalDataset(null);setChat(false)}
 async function refreshSession(){
  setUserMenu(null);
  const session=await me.refetch(),nextUser=session.error?null:session.data?.user;
  if(currentUser?.id!==nextUser?.id||currentUser?.role!==nextUser?.role||currentUser?.active!==nextUser?.active){
   setAdmin(false);setChat(false);setSelectedCard(null);setProposalCard(null);setProposalDataset(null);setDatasetId(null);setToast('');setAnalysisEntry(null);setView('dashboard');
   const protectedQueries={predicate:(query:{queryKey:readonly unknown[]})=>query.queryKey[0]!=='auth-session'};
   await client.cancelQueries(protectedQueries);
   client.removeQueries(protectedQueries);
   if(nextUser)await client.fetchQuery({queryKey:['bootstrap',project],queryFn:()=>api<Bootstrap>(bootstrapPath)}).catch(()=>undefined);
  }else await client.invalidateQueries({queryKey:['bootstrap']});
 }
 function onProposal(p:Proposal){setReviewProposal(p);setChat(false)}
 function appliedProposal(){
  if(!reviewProposal)return;
  if(reviewProposal.proposal_id)setAppliedProposals(ids=>[...ids,reviewProposal.proposal_id!]);
  setView(reviewProposal.card?'dashboard':'data');setReviewProposal(null);
  setToast(reviewProposal.card?(reviewProposal.dataset?'데이터셋과 카드를 저장했습니다.':'카드를 저장했습니다.'):'데이터셋을 저장했습니다.');
  void client.invalidateQueries({queryKey:['bootstrap']});void client.invalidateQueries({queryKey:['dashboard']});void client.invalidateQueries({queryKey:['versions']});
 }

 if(me.isPending)return <div className="initial-loading"><Brand/><Loading label="워크스페이스에 연결 중"/></div>;
 if(me.error||!currentUser)return <AuthGate session={me.data} error={me.error} pending={me.isFetching} onRefresh={refreshSession}/>;
 return <LocalDraftProvider key={`${workspace}-${currentUser.id}-${currentUser.role}`} scope={{workspace,userId:currentUser.id,role:currentUser.role}}><div className="app-shell"><header className="app-header"><Brand/><nav className="workspace-nav" aria-label="주요 기능"><button aria-current={view==='dashboard'||view==='data'?'page':undefined} onClick={()=>setView(projectView.current)}><Layers3 size={17}/>프로젝트</button><button aria-current={view==='knowledge'?'page':undefined} onClick={()=>{setEvaluationKnowledgeReturn(false);setJourney(null);setKnowledgeEntry({area:'knowledge',projectFilter:'all',token:Date.now()});setView('knowledge')}}><BookOpen size={17}/>AI 지식</button></nav><div className="header-actions">{me.data?.mode==='local_admin'&&<span className="test-mode-badge">테스트 관리자</span>}<span className="database-status"><ConnectionSignal {...databaseStatusSignal(dbStatus)}/></span>{currentUser.role==='admin'&&<Tooltip title="워크스페이스 관리"><IconButton onClick={()=>{setAdminTab(0);setAdmin(true)}} aria-label="워크스페이스 관리"><Settings2 size={18}/></IconButton></Tooltip>}<button className="profile-button" onClick={e=>setUserMenu(e.currentTarget)}><span className="user-avatar">{(currentUser.name||currentUser.username).slice(0,1).toUpperCase()}</span><span>{currentUser.name||currentUser.username}<small>{currentUser.role==='admin'?'관리자':currentUser.role==='editor'?'편집자':'조회자'}</small></span><ChevronDown size={13}/></button></div></header>{(view==='dashboard'||view==='data')&&<nav className="project-nav" aria-label="프로젝트 선택"><button className={project===null?'active':''} onClick={()=>switchProject(null)}><Layers3 size={16}/>전체 프로젝트</button><span className="project-nav-separator"/>{projects.map(p=><button key={p.id} className={project===p.id?'active':''} onClick={()=>switchProject(p.id)}><span className={`project-dot project-color-${p.id%3}`}/>{p.name}{p.code!==p.name&&<small>{p.code}</small>}</button>)}<span className="project-nav-end">SHARED WORKSPACE</span></nav>}<main className={`main-content ${view==='data'?'data-view':''}`}>{(view==='dashboard'||view==='data')&&<><div className="page-heading"><div><div className="breadcrumb">WORKSPACE <span>/</span> {projectName?'PROJECT ANALYTICS':'OVERVIEW'}</div><h1>{projectName??'전체 프로젝트'}<span className="heading-period">.</span></h1></div><div className="overview-stats"><div><span>ACTIVE PROJECTS</span><strong>{projects.length??'—'}<small>projects</small></strong></div><i/><div><span>CONNECTED DATA</span><strong>{data?.datasets.length??'—'}<small>datasets</small></strong></div><div className="wafer-emblem"><span/><span/><span/><span/><span/><span/><span/><span/><span/></div></div></div><div className="view-tabs-bar"><div className="view-switch"><button className={view==='dashboard'?'active':''} onClick={()=>setView('dashboard')}><LayoutDashboard size={16}/>대시보드</button><button className={view==='data'?'active':''} onClick={()=>setView('data')}><Table2 size={16}/>데이터 관리</button></div><Button className="project-question-link" startIcon={<BookOpen size={16}/>} onClick={()=>{setJourney(null);setKnowledgeEntry({area:'inbox',projectFilter:project===null?'all':String(project),token:Date.now()});setView('knowledge')}}>{project===null?'AI 지식 확인 요청':'이 프로젝트 확인 요청'}</Button></div><ErrorNotice error={bootstrap.error}/></>}{!workspaceReady?<Loading/>:<>

{(view==='knowledge'||visitedKnowledge)&&<div hidden={view!=='knowledge'}>{evaluationKnowledgeReturn&&<Button startIcon={<Sparkles size={16}/>} onClick={()=>{setEvaluationKnowledgeReturn(false);setChat(true)}}>AI 챗으로 돌아가기</Button>}<SemanticWorkspace key={`knowledge-${workspace}-${currentUser.id}-${currentUser.role}`} workspace={workspace} admin={currentUser.role==='admin'} projects={projects} initialArea={knowledgeEntry.area} initialProjectFilter={knowledgeEntry.projectFilter} entryToken={knowledgeEntry.token} initialQuestionId={knowledgeEntry.questionId} journey={currentJourney} onReturn={()=>void returnToAnalysis()} onJourneyDefinition={observeDefinition} onJourneyInvalid={invalidateJourney} returning={returning} active={view==='knowledge'}/></div>}

{bootstrap.isPending&&(view==='dashboard'||view==='data')?<Loading/>:data&&(view==='dashboard'?<Dashboard key={`board-${workspace}-${project??'common'}`} context={context} period={period} onPeriodChange={changePeriod} datasets={data.datasets} editable={editable} onSelectCard={setSelectedCard} onNotify={setToast} proposalCard={proposalCard} clearProposal={()=>setProposalCard(null)}/>:view==='data'?<DataManager key={`data-${workspace}-${project??'common'}`} projectName={projectName} datasets={data.datasets} catalog={data.catalog} context={context} editable={editable} onSelectDataset={setDatasetId} onNotify={setToast} proposal={proposalDataset} clearProposal={()=>setProposalDataset(null)}/>:null)}
</>}</main><Drawer anchor="right" open={!!analysisPanel} onClose={()=>setAnalysisPanel(null)} slotProps={{paper:{sx:{width:"min(1180px, 96vw)",padding:2}}}}><div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}><h2>{analysisPanel==='activity'?'이전 분석 결과':'분석 확인'}</h2><IconButton onClick={()=>setAnalysisPanel(null)} aria-label="분석 닫기"><X size={20}/></IconButton></div>{workspaceReady&&<>{analysisPanel==='activity'&&<WorkspaceActivity key={`activity-${workspace}-${currentUser.id}-${currentUser.role}-${project??'all'}`} workspace={workspace} userId={currentUser.id} role={currentUser.role} context={context} projects={projects} onProject={id=>{switchProject(id);setAnalysisPanel('activity')}} onOpenAnalysis={openAnalysisResult} onReviewAnalysis={openAnalysisProposal} onOpenCollection={()=>{setAnalysisPanel(null);setJourney(null);setKnowledgeEntry({area:'inbox',projectFilter:'all',token:Date.now()});setView('knowledge')}} onOpenDashboard={()=>{setAnalysisPanel(null);setView('dashboard')}} onOperationAction={openOperation} jump={activityJump}/>}{(analysisPanel==='analytics'||visitedAnalytics)&&<div hidden={analysisPanel!=='analytics'}><AnalyticsWorkspace key={`analytics-${workspace}-${currentUser.id}-${currentUser.role}`} workspace={workspace} canRun={editable} onStart={startAnalysis} start={analysisStart} projectId={project} projects={projects} onRecords={()=>setAnalysisPanel('activity')} initialRunId={selectedAnalysisRun(analysisEntry,workspace,currentUser.id)} resume={analysisJourneyCurrent(analysisResume,workspace,currentUser.id,currentUser.role)?analysisResume:null} visible={analysisPanel==='analytics'} proposal={currentAnalysisProposal} onExitProposal={()=>setAnalysisProposalEntry(null)} onProposalConfirmed={confirmedAnalysis} onProposalResult={openAnalysisResult} onSelectRun={rememberAnalysis} onOpenKnowledge={openAnalysisKnowledge}/></div>}</>}</Drawer><Fab variant="extended" className="assistant-fab" onClick={()=>setChat(true)}><Sparkles size={18}/><span>Project Manager</span><span className="ai-badge">AI</span></Fab><ChatDrawer key={`chat-${workspace}-${project??'common'}-${currentUser.id}-${currentUser.role}`} workspace={workspace} evaluationEntry={evaluationEntry} projectName={projectName} open={chat} onClose={()=>setChat(false)} configured={llmStatus.data?.configured??false} serverConfigured={llmStatus.data?.server_configured??false} onSettings={()=>{setChat(false);setLlmSettings(true)}} editable={editable} context={context} datasetId={chatSelectedDataset(view,selectedCard,selectedDatasetId)} selectedCard={view==='dashboard'?selectedCard:null} onOpenAnalysis={openAnalysisResult} onReviewAnalysis={openAnalysisProposal} onOpenKnowledge={()=>{setChat(false);openAnalysisKnowledge()}} onOpenQuestion={id=>{setChat(false);setEvaluationKnowledgeReturn(true);setJourney(null);setKnowledgeEntry({area:'inbox',projectFilter:'all',questionId:id,token:Date.now()});setView('knowledge')}} onOpenRecords={()=>{setChat(false);setActivityJump(null);setAnalysisPanel('activity')}} onProposal={onProposal} appliedProposals={appliedProposals}/>{reviewProposal&&<AssistantProposalDialog key={reviewProposal.proposal_id} initial={reviewProposal} context={{...context,period_start:reviewProposal.period_start??context.period_start}} datasets={data?.datasets??[]} catalog={data?.catalog??[]} onClose={()=>setReviewProposal(null)} onApplied={appliedProposal}/>}{admin&&<Admin initialTab={adminTab} onProjectsChanged={()=>{switchProject(null);void client.invalidateQueries({queryKey:['bootstrap']});void client.invalidateQueries({queryKey:['dashboard']})}} workspace={workspace} onWorkspaceChanged={reloadWorkspace} currentUser={currentUser} onClose={()=>setAdmin(false)}/>}<Menu anchorEl={userMenu} open={!!userMenu} onClose={()=>setUserMenu(null)}><div className="profile-menu-summary"><strong>{currentUser.name||currentUser.username}</strong><span>{currentUser.team||'소속 정보 없음'}{currentUser.employee_id?` · ${currentUser.employee_id}`:''}</span><small>{currentUser.profile_source==='sso'?'사내 디렉터리에서 동기화한 프로필':currentUser.profile_source==='local'?'로컬 테스트 관리자 계정':'이전 로컬 계정'}</small></div><MenuItem onClick={()=>{setUserMenu(null);setLlmSettings(true)}}><KeyRound size={16}/>LLM API 키 설정</MenuItem><MenuItem onClick={refreshSession}><ShieldCheck size={16}/>로그인 상태 다시 확인</MenuItem></Menu>{llmSettings&&<LlmSettingsDialog key={currentUser.id} userId={currentUser.id} onClose={()=>setLlmSettings(false)}/>}<Snackbar open={!!toast} autoHideDuration={5000} onClose={()=>setToast('')} message={toast} anchorOrigin={{vertical:'bottom',horizontal:'center'}}/></div></LocalDraftProvider>
}
