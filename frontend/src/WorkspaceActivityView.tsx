import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Chip,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ArrowRight,Clock3,RefreshCw} from 'lucide-react';
import {api} from './api';
import {loadAnalysisProposal} from './analysisProposalApi';
import type {AnalysisProposalView} from './analysisProposal.generated';
import type {WorkspaceActivity as ActivityResponse,WorkspaceActivityItem} from './workspaceActivity.generated';
import type {Dashboard,Project,QueryContext} from './types';
import {activityKindNames,activityMatches,activityStatusNames,activitySummary,cardActivity,requiresAttention,validTimestamp} from './workspaceActivity';
import {ErrorNotice,Loading} from './components';
import './workspace-activity.css';
import JobActionButtons from './JobActionButtons';
import AnalysisSchedules from './AnalysisSchedules';
import type {OperationAction,OperationJump} from './operationsState';

function stamp(value:string|null|undefined){const valid=validTimestamp(value);return valid?new Date(valid).toLocaleString('ko-KR'):'기록 없음';}
const cardStatusNames={not_loaded:'아직 조회하지 않음',fetching:'조회 중',failed:'최근 조회 실패',available:'결과 보관 중'};
type Props={workspace:string;userId:string;role:string;context:QueryContext;projects:Project[];onProject:(id:number|null)=>void;onOpenAnalysis:(id:string)=>void;onReviewAnalysis:(plan:AnalysisProposalView)=>void;onOpenCollection:()=>void;onOpenDashboard:()=>void;onOperationAction:(action:OperationAction)=>void;jump:OperationJump|null};
export default function WorkspaceActivity({workspace,userId,role,context,projects,onProject,onOpenAnalysis,onReviewAnalysis,onOpenCollection,onOpenDashboard,onOperationAction,jump}:Props){
 const project=context.project_id??null,client=useQueryClient();
 const activitySection=useRef<HTMLDivElement>(null),scheduleSection=useRef<HTMLDivElement>(null),recoverySection=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(!jump)return;setFilter('all');const node={activity:activitySection,schedules:scheduleSection,recovery:recoverySection}[jump.target].current;node?.scrollIntoView({block:'start'});node?.focus({preventScroll:true})},[jump?.token]);
 const [filter,setFilter]=useState('all'),[opening,setOpening]=useState<string|null>(null),[error,setError]=useState<unknown>(null);
 const [,setCacheRevision]=useState(0),active=useRef(true);
 useEffect(()=>{active.current=true;return()=>{active.current=false}},[]);
 useEffect(()=>client.getQueryCache().subscribe(event=>{if(['added','removed','updated'].includes(event.type)&&event.query.queryKey[0]==='chart')setCacheRevision(v=>v+1)}),[client]);
 const activity=useQuery({queryKey:['workspace-activity',workspace,userId,role,project],queryFn:()=>api<ActivityResponse>(`/workspace/activity${project===null?'':`?project_id=${project}`}`),refetchInterval:q=>q.state.data?.items?.some(item=>['queued','running','cancelling'].includes(item.status))?2000:15000,refetchIntervalInBackground:false,retry:false});
 const board=useQuery({queryKey:['dashboard',project],queryFn:()=>api<Dashboard>(`/dashboards${project===null?'':`?project_id=${project}`}`),retry:false});
 const response=activityMatches(activity.data,workspace,project)&&!activity.isError?activity.data:undefined;
 const items=response?.items??[],summary=activitySummary(items);
 const visible=items.filter(item=>filter==='all'||filter==='attention'&&requiresAttention(item)||filter==='active'&&['queued','running','cancelling'].includes(item.status)||filter==='completed'&&item.status==='completed'||filter==='cancelled'&&item.status==='cancelled');
 const cards=cardActivity(board.isError?[]:board.data?.cards??[],context,client.getQueryCache().getAll());
 const projectName=projects.find(p=>p.id===project)?.name??'프로젝트';
 async function open(item:WorkspaceActivityItem){
  if(opening)return;setError(null);
  if(item.target.kind==='analysis_run'&&item.target.id){onOpenAnalysis(item.target.id);return;}
  if(item.target.kind==='knowledge_collection'){onOpenCollection();return;}
  if(item.target.kind==='analysis_proposal'&&item.target.id){
   setOpening(item.id);
   try{const plan=await loadAnalysisProposal(item.target.id,item.target.project_id??null);
    if(!active.current)return;
    if(plan.workspace_token!==workspace||['unavailable','cancelled'].includes(plan.status))throw new Error('작업공간이나 조건안이 변경되었습니다. 상태를 새로고침해 주세요.');
    if(plan.status==='confirmed'&&plan.run_id)onOpenAnalysis(plan.run_id);else onReviewAnalysis(plan);
   }catch(e){if(active.current)setError(e)}finally{if(active.current)setOpening(null)}
  }
 }
 return <section className="workspace-activity" aria-label="작업 상태와 데이터 시각">
  <div className="activity-heading"><div><span className="activity-eyebrow">ANALYSIS RECORDS</span><h1>분석 기록</h1><p>결과와 연결된 질문·승인을 확인하고, 재시도와 다음 분석으로 이어가세요.</p></div><Button variant="outlined" startIcon={<RefreshCw size={16}/>} disabled={activity.isFetching} onClick={()=>void Promise.all([activity.refetch(),client.invalidateQueries({queryKey:['analytics-schedules']})])}>상태 새로고침</Button></div>
  <div className="activity-filters"><TextField select size="small" label="프로젝트" value={project??'all'} onChange={event=>onProject(event.target.value==='all'?null:Number(event.target.value))}><MenuItem value="all">전체 프로젝트</MenuItem>{projects.map(p=><MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}</TextField><TextField select size="small" label="작업 상태" value={filter} onChange={event=>setFilter(event.target.value)}><MenuItem value="all">모든 상태</MenuItem><MenuItem value="active">대기·진행 중</MenuItem><MenuItem value="attention">확인 필요·실패</MenuItem><MenuItem value="completed">완료</MenuItem><MenuItem value="cancelled">취소 완료</MenuItem></TextField><span>상태 확인: {stamp(response?.generated_at)}</span></div>
  <div ref={scheduleSection} tabIndex={-1} className="activity-section-target"><details className="records-optional"><summary>변화 알림과 정기 분석</summary>{['admin','editor'].includes(role)&&<AnalysisSchedules workspace={workspace} project={project} projects={projects} onOpenRun={onOpenAnalysis}/>}</details></div>
  <div ref={activitySection} tabIndex={-1} className="activity-section-target">
  <ErrorNotice error={activity.error??error}/>
  {activity.isPending?<Loading label="저장된 작업 상태를 확인하는 중"/>:response&&<>
   <div className="activity-summary" aria-label="표시된 작업 요약"><div><span>대기·진행 중</span><strong>{summary.active}</strong></div><div><span>확인 필요·실패</span><strong>{summary.attention}</strong></div><div><span>완료</span><strong>{summary.completed}</strong></div><small>최근 표시된 {items.length}건 기준{response.has_more?' · 이전 작업은 각 기능의 이력에서 확인':''}</small></div>
   <Alert severity="info" icon={<Clock3 size={20}/>}>상태 확인 시각은 데이터 조회 시각과 다릅니다. 마지막 측정 시각은 각 분석이 읽은 범위의 기록이며, 원천 전체가 최신이라는 뜻은 아닙니다.</Alert>
   {!!response.notes?.length&&<ul className="activity-notes">{response.notes.map(note=><li key={note}>{note}</li>)}</ul>}
   <div className="activity-list" aria-label="최근 작업 목록">{!visible.length?<p className="activity-empty">현재 범위에 해당하는 작업이 없습니다.</p>:visible.map(item=><article className="activity-item" key={`${item.kind}:${item.id}`}>
    <div className="activity-item-heading"><div><small>{activityKindNames[item.kind]} · {item.project_name??(item.project_id===null||item.project_id===undefined?'공통':`프로젝트 ${item.project_id}`)}</small><h2>{item.title}</h2></div><Chip size="small" color={item.status==='failed'?'error':requiresAttention(item)?'warning':item.status==='running'?'info':'default'} label={activityStatusNames[item.status]}/></div>
    {item.message&&<p>{item.message}</p>}
    <dl><div><dt>작업 갱신</dt><dd>{stamp(item.activity_at)}</dd></div><div><dt>원천 조회</dt><dd>{stamp(item.freshness?.queried_at)}</dd></div><div><dt>읽은 범위의 마지막 측정</dt><dd>{stamp(item.freshness?.last_measured_at)}</dd></div></dl>
    <div className="activity-item-footer"><span>{(item.question_count??0)>0?`연결된 질문 ${item.question_count}개`:item.kind==='analysis_proposal'?'조건 확인 전에는 계산하지 않습니다.':''}</span><Button size="small" endIcon={<ArrowRight size={15}/>} disabled={!!opening||item.kind==='analysis_proposal'&&item.status==='cancelled'} onClick={()=>void open(item)}>{opening===item.id?'확인 중…':item.kind==='analysis_proposal'?'조건 확인·수정':item.kind==='knowledge_collection'?'지식 확인 요청 열기':'결과·질문 확인'}</Button></div>
    <JobActionButtons key={`${workspace}:${item.id}`} target={item.target} controls={item.controls} workspace={workspace} onOpenRun={onOpenAnalysis}/>
   </article>)}</div>
  </>}
  </div>
  <details className="records-optional"><summary>대시보드 조회 시각 확인</summary><div className="activity-section-heading"><div><h2>대시보드 데이터 시각</h2><p>{project===null?'전체 프로젝트의 공통 보드':projectName+' 보드'} · 현재 선택한 조회 기간 · 이 브라우저에서 확인한 결과</p></div><Button size="small" onClick={onOpenDashboard} endIcon={<ArrowRight size={15}/>}>대시보드 열기</Button></div>
  <ErrorNotice error={board.error}/>
  <p className="activity-help">카드를 자동으로 재조회하지 않습니다. 원천의 측정 시각을 제공하지 않는 카드에는 ‘기록 없음’으로 표시합니다. 간트의 기준 시각은 조회에 사용한 스냅샷입니다.</p>
  {board.isPending?<Loading/>:<div className="activity-table-wrap"><table className="activity-table"><thead><tr><th>카드</th><th>조회 상태</th><th>결과 조회 시각</th><th>간트 기준 시각</th></tr></thead><tbody>{cards.map(card=><tr key={card.id}><td>{card.title}{card.datasetVersion!==null&&<small>데이터셋 v{card.datasetVersion}</small>}{card.limitedGanttScope&&<small>최근 확인한 간트 검색·페이지 범위</small>}</td><td>{cardStatusNames[card.state]}{card.cached&&<small>캐시 결과</small>}{card.state==='failed'&&card.queriedAt&&<small>이전 성공 결과의 시각</small>}</td><td>{stamp(card.queriedAt)}</td><td>{stamp(card.sourceAt)}</td></tr>)}{!cards.length&&!board.isError&&<tr><td colSpan={4}>등록된 카드가 없습니다.</td></tr>}</tbody></table></div>}
 </details></section>;
}
