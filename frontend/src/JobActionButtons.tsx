import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,DialogTitle} from '@mui/material';
import {useQueryClient} from '@tanstack/react-query';
import {post} from './api';
import {ErrorNotice} from './components';
import {jobActionBody,jobActionCurrent,jobActionExplanation} from './jobActions';
import './job-actions.css';
import type {ActivityActionRequest,ActivityActionResult,ActivityControls,ActivityTarget} from './workspaceActivity.generated';

export default function JobActionButtons({target,controls,workspace,onOpenRun,onChanged}:{target:ActivityTarget;controls?:ActivityControls|null;workspace:string;onOpenRun?:(id:string)=>void;onChanged?:()=>void}){
 const client=useQueryClient(),alive=useRef(true),pending=useRef(false);
 const [choice,setChoice]=useState<ActivityActionRequest|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[notice,setNotice]=useState('');
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 const context=JSON.stringify([workspace,target.kind,target.id,target.project_id??null]),current=useRef(context);current.current=context;
 useEffect(()=>{setNotice('')},[controls?.revision,context]);
 const valid=!!choice&&jobActionCurrent(choice,target,controls,workspace);
 async function act(){
  if(!choice||!valid||pending.current)return;
  pending.current=true;setBusy(true);setError(null);const captured=context;
  try{const result=await post<ActivityActionResult>('/workspace/activity/actions',choice);
   await Promise.all(['workspace-activity','analytics','semantic','analytics-schedules','analytics-history','analytics-comparison'].map(key=>client.invalidateQueries({queryKey:[key]})));
   if(!alive.current||captured!==current.current)return;
   setChoice(null);setNotice(result.message);onChanged?.();
   if(choice.action==='retry'&&result.target.kind==='analysis_run'&&result.target.id)onOpenRun?.(result.target.id);
  }catch(e){
   void client.invalidateQueries({queryKey:['workspace-activity']});void client.invalidateQueries({queryKey:['analytics']});
   if(alive.current&&captured===current.current)setError(e);
  }finally{pending.current=false;if(alive.current&&captured===current.current)setBusy(false)}
 }
 return <div className="job-actions">
  {controls?.can_cancel&&<Button size="small" color="warning" disabled={busy} onClick={()=>{setError(null);setChoice(jobActionBody(target,controls,'cancel',workspace))}}>작업 취소</Button>}
  {controls?.can_retry&&<Button size="small" variant="outlined" disabled={busy} onClick={()=>{setError(null);setChoice(jobActionBody(target,controls,'retry',workspace))}}>다시 시도</Button>}
  {controls?.retry_run_id&&onOpenRun&&<Button size="small" onClick={()=>onOpenRun(controls.retry_run_id!)}>재시도 작업 열기</Button>}
  {!controls?.can_retry&&controls?.retry_after&&Date.parse(controls.retry_after)>Date.now()&&<small>재시도 가능: {new Date(controls.retry_after).toLocaleTimeString('ko-KR')}</small>}
  {notice&&<Alert severity="info" onClose={()=>setNotice('')}>{notice}</Alert>}
  {choice&&<Dialog open onClose={()=>{if(!busy)setChoice(null)}} maxWidth="sm" fullWidth>
   <DialogTitle>{choice?.action==='cancel'?'작업을 취소하시겠습니까?':'같은 작업을 다시 시도하시겠습니까?'}</DialogTitle>
   <DialogContent><p>{choice&&jobActionExplanation(choice.target,choice.action)}</p>{!valid&&!busy&&<Alert severity="warning">작업 상태가 변경되었습니다. 닫은 뒤 최신 상태에서 다시 선택하세요.</Alert>}<ErrorNotice error={error}/></DialogContent>
   <DialogActions><Button disabled={busy} onClick={()=>setChoice(null)}>닫기</Button><Button variant="contained" color={choice?.action==='cancel'?'warning':'primary'} disabled={busy||!valid} onClick={()=>void act()}>{busy?'처리 중…':choice?.action==='cancel'?'취소 요청':'재시도 시작'}</Button></DialogActions>
  </Dialog>}
 </div>;
}
