import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,post} from './api';
import {ErrorNotice,Loading} from './components';
import type {AnalysisRun} from './analyticsTypes';
import type {AuthSession} from './types';
import type {ScheduleAction,ScheduleList,SchedulePreview,ScheduleView} from './analysisSchedules.generated';
import {canSchedule,compatibleSchedules,scheduleIntervals,scheduleSave} from './analysisScheduleState';
import './analysis-schedules.css';
import Periods from './AnalysisSchedulePeriods';
import AnalysisHistoryDialog from './AnalysisHistoryDialog';

const stamp=(value:string|null|undefined)=>value?new Date(value).toLocaleString('ko-KR'):'—';
const intervalLabel=(hours:number)=>scheduleIntervals.find(i=>i.value===hours)?.label??`${hours}시간마다`;
export function useSchedules(workspace:string,enabled=true,project:number|null=null){
 const session=useQueryClient().getQueryData<AuthSession>(['auth-session']),actor=session?.user;
 return useQuery({queryKey:['analytics-schedules',workspace,project,actor?.id,actor?.role],queryFn:({signal})=>api<ScheduleList>(`/analytics/schedules${project===null?'':`?project_id=${project}`}`,{signal}),enabled:enabled&&!!workspace&&!!actor&&['admin','editor'].includes(actor.role),refetchInterval:15000,refetchIntervalInBackground:false,retry:false});
}


export function ScheduleAnalysisButton({run,workspace}:{run:AnalysisRun;workspace:string}){
 const [open,setOpen]=useState(false),[interval,setInterval]=useState<1|6|24|168>(24),[name,setName]=useState(`${run.request.parameter??'PARA'} 정기 분석`);
 const [target,setTarget]=useState<{id:string;revision:number}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[saved,setSaved]=useState(false);
 const alive=useRef(true),pending=useRef(false),client=useQueryClient();
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 const schedules=useSchedules(workspace,open);
 const preview=useQuery({queryKey:['analytics-schedule-preview',workspace,run.id,run.revision,interval],queryFn:()=>post<SchedulePreview>('/analytics/schedules/preview',{source_run_id:run.id,interval_hours:interval,workspace_token:workspace}),enabled:open,retry:false,refetchOnWindowFocus:false,staleTime:0});
 const compatible=compatibleSchedules(schedules.data?.workspace_token===workspace?schedules.data.items:[],run);
 const selected=compatible.find(s=>s.id===target?.id);
 const validTarget=!target||selected?.revision===target.revision;
 const body=preview.data?scheduleSave(preview.data,run,workspace,interval,name):null;
 async function save(){
  if(!body||!validTarget||pending.current||preview.isFetching)return;
  pending.current=true;setBusy(true);setError(null);
  try{
   if(target)await post(`/analytics/schedules/${target.id}/actions`,{action:'replace',expected_revision:target.revision,workspace_token:workspace,replacement:body} satisfies ScheduleAction);
   else await post('/analytics/schedules',body);
   await client.invalidateQueries({queryKey:['analytics-schedules']});
   if(alive.current){setOpen(false);setSaved(true)}
  }catch(e){if(alive.current)setError(e)}finally{pending.current=false;if(alive.current)setBusy(false)}
 }
 if(!canSchedule(run))return null;
 return <div className="schedule-create"><Button size="small" onClick={()=>{setOpen(true);setError(null);setSaved(false)}}>정기 분석 설정</Button>{saved&&<Alert severity="success">정기 분석을 저장했습니다. 작업 상태에서 일정과 알림을 확인할 수 있습니다.</Alert>}
 {open&&<Dialog open fullWidth maxWidth="md" onClose={()=>{if(!busy)setOpen(false)}}><DialogTitle>확정한 조건으로 정기 분석</DialogTitle><DialogContent>
 <div className="schedule-fields"><TextField label="정기 분석 이름" value={name} disabled={busy} onChange={e=>setName(e.target.value)} inputProps={{maxLength:120}}/>
 <TextField select label="실행 간격" value={interval} disabled={busy} onChange={e=>setInterval(Number(e.target.value) as typeof interval)}>{scheduleIntervals.map(i=><MenuItem value={i.value} key={i.value}>{i.label}</MenuItem>)}</TextField>
 <TextField select label="저장할 일정" value={target?.id??'new'} disabled={busy||schedules.isPending} onChange={e=>{const selected=compatible.find(s=>s.id===e.target.value);setTarget(selected?{id:selected.id,revision:selected.revision}:null);if(selected){setName(selected.name);setInterval(selected.interval_hours as typeof interval)}}}><MenuItem value="new">새 정기 분석</MenuItem>{compatible.map(s=><MenuItem key={s.id} value={s.id}>{s.name} · {s.enabled?'예약 중':'일시정지'} — 조건 교체·재개</MenuItem>)}</TextField></div>
 <p>범위: {run.request._project_scope?.name??(run.request.project_id?`프로젝트 ${run.request.project_id}`:'공통')} · 완료된 분석의 조건과 승인 근거를 사용합니다.</p>
 <ErrorNotice error={error??preview.error??schedules.error}/>{preview.isFetching?<Loading label="첫 실행의 기간을 확인하는 중"/>:preview.data&&<><h3>첫 실행: {stamp(preview.data.first_run_at)}</h3><Periods request={preview.data.request}/><ul className="schedule-notes">{preview.data.notes.map(note=><li key={note}>{note}</li>)}</ul></>}
 {!validTarget&&<Alert severity="warning">선택한 일정이 변경되었습니다. 목록에서 다시 선택하세요.</Alert>}
 </DialogContent><DialogActions><Button disabled={busy} onClick={()=>setOpen(false)}>닫기</Button><Button variant="contained" disabled={busy||!body||!validTarget||preview.isFetching||preview.isError||schedules.isError} onClick={()=>void save()}>{busy?'저장 중…':target?'확인한 조건 적용·예약 재개':'확인한 조건으로 예약'}</Button></DialogActions></Dialog>}
 </div>;
}

export default function AnalysisSchedules({workspace,project,projects,onOpenRun}:{workspace:string;project:number|null;projects:Array<{id:number;name:string}>;onOpenRun:(id:string)=>void}){
 const query=useSchedules(workspace,true,project),client=useQueryClient(),alive=useRef(true),pending=useRef(false);
 const [busy,setBusy]=useState<string|null>(null),[error,setError]=useState<unknown>(null);
 const [history,setHistory]=useState<{id:string;name:string;runId?:string}|null>(null);
 useEffect(()=>{setHistory(null)},[workspace,project]);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 const data=query.data?.workspace_token===workspace&&!query.isError?query.data:undefined;
 async function act(id:string,body:ScheduleAction|{workspace_token:string},notification=false){
  if(pending.current)return;pending.current=true;setBusy(id);setError(null);
  try{await post(`/analytics/schedules/${notification?'notifications/':''}${id}/${notification?'ack':'actions'}`,body);await client.invalidateQueries({queryKey:['analytics-schedules']})}
  catch(e){if(alive.current)setError(e)}finally{pending.current=false;if(alive.current)setBusy(null)}
 }
 return <section className="analysis-schedules" aria-label="정기 분석과 변화 알림"><div className="activity-section-heading"><div><h2>정기 분석·변화 알림</h2><p>완료한 분석 결과에서 ‘정기 분석 설정’으로 등록합니다. 앱이 켜져 있고 해당 DB가 선택된 동안 실행합니다.</p></div><Chip label={`미확인 ${data?.unread_count??0}개`}/></div>
 <ErrorNotice error={error??query.error}/>{query.isPending?<Loading/>:data&&<>
 <div className="schedule-notifications" aria-label="분석 알림">{data.notifications.length===0?<p className="activity-help">등록된 알림이 없습니다. 변화 판정이 달라지거나 실행 확인이 필요하면 여기에 표시합니다.</p>:data.notifications.map(n=><article key={n.id} className={n.acknowledged_at?'schedule-notice acknowledged':'schedule-notice'}><div><strong>{n.schedule_name}</strong><small>{stamp(n.created_at)} · {n.acknowledged_at?'확인 완료':'미확인'}</small><p>{n.message}</p></div><div>{n.kind==='changed'&&n.run_id&&<Button size="small" onClick={()=>setHistory({id:n.schedule_id,name:n.schedule_name,runId:n.run_id!})}>변화 근거 비교</Button>}{n.run_id&&<Button size="small" onClick={()=>onOpenRun(n.run_id!)}>결과·질문 확인</Button>}{!n.acknowledged_at&&<Button size="small" disabled={!!busy} onClick={()=>void act(n.id,{workspace_token:workspace},true)}>확인 완료</Button>}</div></article>)}</div>
 {data.has_more_notifications&&<p>최근 알림 50개를 표시합니다. 미확인 알림을 먼저 표시합니다.</p>}
 <div className="schedule-list">{data.items.map(s=><article key={s.id} className="schedule-item"><div className="activity-item-heading"><div><small>{s.project_id===null?'공통':projects.find(p=>p.id===s.project_id)?.name??`프로젝트 ${s.project_id}`} · {intervalLabel(s.interval_hours)}</small><h3>{s.name}</h3></div><Chip size="small" color={s.enabled?'info':'default'} label={s.enabled?'예약 중':'일시정지'}/></div><p>다음 실행: {stamp(s.next_run_at)}</p><p>{s.message}</p><details><summary>확정한 원래 조건</summary><Periods request={s.request}/><p>실제 실행에서는 마지막 종료 시각을 예약 시각으로 이동합니다.</p></details><div className="schedule-actions"><Button size="small" onClick={()=>setHistory({id:s.id,name:s.name})}>실행 이력·결과 비교</Button>{s.last_run_id&&<Button size="small" onClick={()=>onOpenRun(s.last_run_id!)}>최근 결과·재시도·취소</Button>}<Button size="small" onClick={()=>onOpenRun(s.origin_run_id)}>원래 분석·조건 변경</Button><Button size="small" disabled={!!busy||!s.can_manage} onClick={()=>void act(s.id,{action:s.enabled?'pause':'resume',expected_revision:s.revision,workspace_token:workspace})}>{s.enabled?'예약 일시정지':'같은 조건으로 예약 재개'}</Button></div></article>)}</div>
 {data.items.length>0&&<p className="activity-help">예약 일시정지는 다음 실행을 멈춥니다. 진행 중 작업은 별도로 취소할 수 있습니다. 질문·승인 후에는 완료된 새 분석에서 기존 일정을 선택해 조건을 교체하세요.</p>}
 </>}
 {history&&<AnalysisHistoryDialog key={`${workspace}:${project}:${history.id}`} workspace={workspace} scheduleId={history.id} name={history.name} initialRunId={history.runId} onClose={()=>setHistory(null)} onOpenRun={id=>{setHistory(null);onOpenRun(id)}}/>}
 </section>;
}
