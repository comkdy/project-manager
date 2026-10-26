import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,post} from './api';
import {ErrorNotice,Loading} from './components';
import type {BackupView,RecoveryRequest,RecoveryStatus} from './recovery.generated';
import {RecoveryRequests,recoveryLabels,recoveryMatches} from './recoveryState';
import './recovery.css';

const stamp=(value:string|null)=>value?new Date(value).toLocaleString('ko-KR'):'아직 없음';
export default function RecoveryPanel({workspace,userId}:{workspace:string;userId:string}){
 const client=useQueryClient(),alive=useRef(true),requests=useRef(new RecoveryRequests());
 const [confirm,setConfirm]=useState('create'),[open,setOpen]=useState(false),[pending,setPending]=useState(false),[error,setError]=useState<unknown>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 const query=useQuery({queryKey:['admin-recovery',workspace,userId],queryFn:({signal})=>api<RecoveryStatus>('/admin/recovery',{signal}),retry:false,refetchInterval:q=>q.state.data?.busy?2000:15000,refetchIntervalInBackground:false});
 const data=!query.isError&&recoveryMatches(query.data,workspace)?query.data:undefined;
 const last=data?.items.find(item=>item.checked_at!==null);
 const allowed=!!data&&!pending&&(confirm==='create'?data.can_create:data.items.some(item=>item.id===confirm&&item.can_verify));
 async function run(){
  if(!allowed||!open)return;
  const requestId=requests.current.begin(confirm);if(!requestId)return;
  setPending(true);setError(null);let success=false;
  try{
   await post<BackupView>(`/admin/recovery${confirm==='create'?'':`/${confirm}/verify`}`,{workspace_token:workspace,request_id:requestId} satisfies RecoveryRequest);
   success=true;if(alive.current)setOpen(false);
   await client.invalidateQueries({queryKey:['admin-recovery']});
  }catch(e){if(alive.current)setError(e)}finally{requests.current.finish(success);if(alive.current)setPending(false)}
 }
 return <section className="recovery-panel" aria-label="관리 데이터 백업과 복구 준비">
  <div className="activity-section-heading"><div><h2>백업·복구 준비</h2><p>관리자 전용 · 관리 DB 전체 · 위 프로젝트 선택과 무관하게 모든 원천 DB의 관리 기록을 포함합니다.</p></div><Button variant="outlined" disabled={!data?.can_create||pending} onClick={()=>{setError(null);setConfirm('create');setOpen(true)}}>백업하고 복구 검증</Button></div>
  <ErrorNotice error={query.error}/>{query.isPending?<Loading label="백업 상태 확인 중"/>:data&&<>
   {!data.supported?<Alert severity="info">{data.reason}</Alert>:<div className="recovery-summary"><div><small>최근 검증한 백업 생성 시각</small><strong>{stamp(last?.created_at??null)}</strong><span>{last?`${recoveryLabels[last.status]} · 검증 ${stamp(last.checked_at)}`:'백업을 만들어 복구 가능 여부를 확인하세요.'}</span></div><div><Chip size="small" color={data.busy?'info':'default'} label={data.busy?'백업·검증 진행 중':`백업 ${data.backup_count} / 50개`}/><Chip size="small" color="warning" variant="outlined" label="암호화 키·외부 보관 별도 준비"/></div></div>}
   {data.supported&&!data.can_create&&!data.busy&&data.backup_count>=50&&<Alert severity="warning">보관 한도에 도달했습니다. 백업을 별도 보관한 후 운영자가 폴더를 정리하세요. 자동 삭제하지 않습니다.</Alert>}
   <details className="recovery-guide"><summary>백업 범위와 복구 준비 안내</summary><ul>{data.notes.map(note=><li key={note}>{note}</li>)}</ul></details>
   <div className="recovery-records">{data.items.map(item=><details className="recovery-record" key={item.id} open={item.status==='running'||undefined}><summary><span>{stamp(item.created_at)}</span><Chip component="span" size="small" color={item.status==='verified'?'success':item.status==='running'?'info':item.status==='failed'?'error':'warning'} label={recoveryLabels[item.status]}/><span>{item.table_count}개 테이블 · {item.row_count.toLocaleString()}행</span></summary><div className="recovery-record-body"><p role={item.status==='running'?'status':undefined}>{item.stage} · {item.message}</p><p>검증 시각: {stamp(item.checked_at)} · 파일 크기: {(item.size_bytes/1024/1024).toFixed(2)} MB</p><ul className="recovery-checks">{item.checks.map(check=><li key={check.label}><strong>{check.status==='passed'?'✓':'!'} {check.label}</strong><span>{check.message}</span></li>)}</ul><p className="recovery-folder">작업공간 내 보관 폴더: {item.folder}</p><Button size="small" disabled={!item.can_verify||pending} onClick={()=>{setError(null);setConfirm(item.id);setOpen(true)}}>복구 검증 다시 실행</Button></div></details>)}</div>
  </>}
  <Dialog open={open} onClose={()=>{if(!pending)setOpen(false)}} fullWidth maxWidth="sm" aria-labelledby="recovery-confirm-title"><DialogTitle id="recovery-confirm-title">{confirm==='create'?'관리 DB 전체 백업·복구 검증':'저장된 백업 다시 검증'}</DialogTitle><DialogContent><p>{confirm==='create'?'모든 프로젝트의 설정과 분석·질문·승인·실행 이력·알림을 이 PC에 저장하고, 임시 DB에서 복원을 검증합니다.':'저장 당시의 내용을 임시 DB에 복원해 다시 검증합니다. 현재 앱의 변경 사항이 백업에 추가되지는 않습니다.'}</p><p>기존 DB와 예약은 유지됩니다. 원천 측정값과 브라우저 초안은 포함하지 않습니다.</p><Alert severity="warning">암호화 키와 .env는 포함하지 않습니다. 접속 정보 복구에 필요한 암호화 키와 백업 파일을 별도로 안전하게 보관하세요. 외부 보관 여부는 확인하지 않습니다.</Alert><p>백업 파일 자체는 암호화하지 않으며 이 PC의 폴더 접근 권한을 따릅니다.</p><ErrorNotice error={error}/>{data?.busy&&<Alert severity="info">이미 작업이 진행 중입니다. 닫은 뒤 진행 상태를 확인하세요.</Alert>}</DialogContent><DialogActions><Button disabled={pending} onClick={()=>setOpen(false)}>닫기</Button><Button variant="contained" disabled={!allowed} onClick={()=>void run()}>{pending?'요청 중…':confirm==='create'?'백업·검증 시작':'다시 검증 시작'}</Button></DialogActions></Dialog>
 </section>;
}
