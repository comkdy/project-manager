import {useEffect,useState} from 'react';
import {Alert,Button,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,post} from './api';
import {ErrorNotice} from './components';
import type {EvaluationAnalysisView} from './evaluationAnalysis';

export interface KnowledgeWatch {id:string;origin_run_id:string;revision:number;project_id:number|null;enabled:boolean;interval_minutes:number;name:string;case_name:string;message:string;available:boolean;next_check_at:string;checked_at:string|null;last_run_id:string|null;last_status:string|null;review:{question_id:string;status:string}|null}

export default function KnowledgeWatchControls({workspace,run,onOpenRun,onOpenQuestion,active=true}:{workspace:string;run?:EvaluationAnalysisView;onOpenRun?:(id:string,projectId:number|null)=>void;onOpenQuestion:(id:string)=>void;active?:boolean}){
 const client=useQueryClient(),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[minutes,setMinutes]=useState(60);
 const list=useQuery({queryKey:['knowledge-watches',workspace],queryFn:({signal})=>api<KnowledgeWatch[]>('/knowledge-watches',{signal}),enabled:active,refetchInterval:active?15000:false});
 async function change(path:string,body:unknown){setBusy(true);setError(null);try{await post(path,body);await client.invalidateQueries({queryKey:['knowledge-watches',workspace]});await client.invalidateQueries({queryKey:['semantic']})}catch(e){setError(e)}finally{setBusy(false)}}
 const rows=(list.data??[]).filter(w=>!run||w.last_run_id===run.id||w.origin_run_id===run.id);
 useEffect(()=>{
  if(run&&active&&rows.some(w=>w.last_run_id===run.id&&w.review&&(w.review.question_id!==run.review?.question_id||w.review.status!==run.review?.status)))void client.invalidateQueries({queryKey:['evaluation-analysis']});
 },[list.data,run?.id,run?.review?.question_id,run?.review?.status,active,client]);
 return <section className="evaluation-analysis-review" aria-label="새 데이터 지속 분석"><h3>새 데이터 지속 분석</h3><p>승인된 사례와 확인한 분석 조건을 사용합니다. 변경 확인 주기마다 데이터가 달라졌는지 확인하고, 바뀐 경우에만 다시 계산합니다. 같은 데이터로 분석을 반복하지 않습니다. 결과는 확인 요청에 보냅니다.</p>
 <ErrorNotice error={error??list.error}/>
 {run&&run.status==='completed'&&run.result?.data_fingerprint&&!rows.length&&<div>
  <TextField select size="small" label="변경 확인 주기" value={minutes} disabled={busy} onChange={e=>setMinutes(Number(e.target.value))}>{[[15,'15분'],[60,'1시간'],[360,'6시간'],[1440,'하루']].map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField>{' '}
  <Button variant="outlined" disabled={busy||list.isFetching} onClick={()=>void change('/knowledge-watches',{run_id:run.id,expected_revision:run.revision,workspace_token:workspace,interval_minutes:minutes})}>이 조건으로 지속 분석 시작</Button>
 </div>}
 {!run&&!rows.length&&<p>아직 지속 분석이 없습니다. 아래에서 승인 사례로 데이터와 분석 조건을 추천받아 시작하세요.</p>}
 {rows.map(w=><article key={w.id} style={{borderTop:'1px solid #dce5e5',marginTop:16,paddingTop:12}}><strong>{w.name}</strong><p>참고 사례: {w.case_name||'재확인 필요'}</p><Alert severity={w.enabled?'info':'warning'}>{w.message||'데이터를 확인할 예정입니다.'}</Alert><p>{w.enabled?`${w.interval_minutes}분마다 변경 확인`:'일시정지'}{w.checked_at?` · 최근 확인 ${new Date(w.checked_at).toLocaleString('ko-KR')}`:''}</p>
 <Button disabled={busy||list.isFetching} onClick={()=>void change(`/knowledge-watches/${w.id}/actions`,{action:w.enabled?'pause':'resume',expected_revision:w.revision,workspace_token:workspace})}>{w.enabled?'일시정지':'재개'}</Button>
 {w.enabled&&<Button disabled={busy||list.isFetching} onClick={()=>void change(`/knowledge-watches/${w.id}/actions`,{action:'check',expected_revision:w.revision,workspace_token:workspace})}>지금 데이터 확인</Button>}
 {w.last_run_id&&onOpenRun&&<Button onClick={()=>onOpenRun(w.last_run_id!,w.project_id)}>최근 분석 결과</Button>}
 {w.review&&<Button onClick={()=>onOpenQuestion(w.review!.question_id)}>결과 검토·승인</Button>}
 </article>)}
 <small>앱 서버가 실행 중이고 해당 DB가 선택되어 있을 때 확인합니다. 데이터셋 정의·승인 지식이 바뀌면 자동으로 멈추고 재확인을 안내합니다.</small></section>;
}
