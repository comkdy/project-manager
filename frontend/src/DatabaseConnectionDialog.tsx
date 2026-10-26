import {useId,useRef,useState} from 'react';
import {Button,Dialog,DialogActions,DialogContent,IconButton,MenuItem,TextField} from '@mui/material';
import {Database,Save,X} from 'lucide-react';
import {api} from './api';
import {ErrorNotice} from './components';

export interface DatabaseProfile {id:string;host:string;port:number;database:string;username:string;sslmode:string}
export interface ConnectionState {state:'connected'|'checking'|'error'|'idle';label:string}
export interface ConnectionResult {profile_id:string;workspace_token:string;table_count:number;active:boolean}
type Form=Omit<DatabaseProfile,'id'|'port'> & {port:string;password:string};
const emptyForm:Form={host:'',port:'5432',database:'',username:'',password:'',sslmode:'prefer'};

export function ConnectionSignal({state,label}:ConnectionState){
 return <span className={`db-signal db-signal-${state}`} role="status"><i aria-hidden="true"/>{label}</span>
}

export default function DatabaseConnectionDialog({profile,status,onClose,onSaved,onChecked}:{
 profile:DatabaseProfile|null;status:ConnectionState;onClose:()=>void;
 onSaved:(result:ConnectionResult)=>Promise<void>;onChecked:()=>Promise<void>;
}){
 const formRef=useRef<HTMLFormElement>(null),formId=useId(),titleId=useId();
 const [form,setForm]=useState<Form>(()=>profile?{
  host:profile.host,port:String(profile.port),database:profile.database,username:profile.username,sslmode:profile.sslmode,password:'',
 }:emptyForm);
 const [busy,setBusy]=useState<'test'|'save'|null>(null),[error,setError]=useState<unknown>(null);
 const [signal,setSignal]=useState<ConnectionState|null>(null),[dirty,setDirty]=useState(false);
 function close(){if(!busy)onClose()}
 function change(key:keyof Form,value:string){setForm(f=>({...f,[key]:value}));setDirty(true);setSignal(null);setError(null)}
 async function connect(action:'test'|'save'){
  if(!formRef.current?.reportValidity())return;
  setBusy(action);setError(null);setSignal({state:'checking',label:action==='test'?'연결 확인 중':'접속 정보 확인 및 저장 중'});
  try{
   const result=await api<ConnectionResult>(action==='test'?'/admin/databases/test':'/admin/databases',{
    method:'POST',body:JSON.stringify({...form,port:Number(form.port),profile_id:profile?.id??null,password:form.password||null}),
   });
   if(action==='save'){
    setForm(f=>({...f,password:''}));
    await onSaved(result);
    onClose();
   }else{
    setSignal({state:'connected',label:`연결 확인 완료 · 테이블 ${result.table_count}개`});
    if(!dirty)await onChecked();
   }
  }catch(e){setError(e);setSignal({state:'error',label:action==='test'?'연결 확인 실패':'저장 실패'})}
  finally{setBusy(null)}
 }
 const currentSignal=signal??(dirty?{state:'idle' as const,label:'변경한 접속 정보 확인 필요'}:status);
 return <Dialog open fullWidth maxWidth="sm" onClose={close} disableEscapeKeyDown={!!busy}
  aria-labelledby={titleId} slotProps={{paper:{className:'db-connection-dialog'}}}>
  <div className="dialog-heading"><div className="dialog-icon"><Database size={21}/></div>
   <h2 id={titleId}>{profile?'DB 접속 정보':'DB 추가'}</h2>
   <IconButton aria-label="접속 정보 닫기" onClick={close} disabled={!!busy}><X size={20}/></IconButton>
  </div>
  <DialogContent>
   <form id={formId} ref={formRef} className="db-connection-form" onSubmit={e=>{e.preventDefault();void connect('save')}} autoComplete="off">
    <div className="db-dialog-status"><ConnectionSignal {...currentSignal}/></div>
    <fieldset className="db-connection-fields" disabled={!!busy}>
     <div className="db-fields">
      <TextField required label="Host" value={form.host} onChange={e=>change('host',e.target.value)} placeholder="db.company.local"/>
      <TextField required label="Port" type="number" value={form.port} onChange={e=>change('port',e.target.value)} slotProps={{htmlInput:{min:1,max:65535}}}/>
      <TextField required label="DB name" value={form.database} onChange={e=>change('database',e.target.value)}/>
      <TextField required label="User" value={form.username} onChange={e=>change('username',e.target.value)} autoComplete="off"/>
      <TextField required={!profile} label="Password" type="password" value={form.password} onChange={e=>change('password',e.target.value)} placeholder={profile?'저장된 비밀번호 사용':''} autoComplete="new-password"/>
     </div>
     <details className="db-ssl"><summary>SSL 설정</summary><TextField select label="SSL" value={form.sslmode} onChange={e=>change('sslmode',e.target.value)} fullWidth>
      <MenuItem value="prefer">자동 (prefer)</MenuItem><MenuItem value="require">암호화 필수 (require)</MenuItem><MenuItem value="verify-full">서버 인증서 검증 (verify-full)</MenuItem><MenuItem value="disable">사용 안 함 (disable)</MenuItem>
     </TextField></details>
    </fieldset>
    <ErrorNotice error={error}/>
   </form>
  </DialogContent>
  <DialogActions className="db-form-actions" disableSpacing>
   <Button variant="outlined" onClick={()=>void connect('test')} disabled={!!busy}>연결 확인</Button>
   <Button form={formId} type="submit" variant="contained" startIcon={<Save size={16}/>} disabled={!!busy}>{busy==='save'?'저장 중…':profile?'저장':'DB 등록'}</Button>
  </DialogActions>
 </Dialog>
}
