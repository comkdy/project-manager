import {useState} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,IconButton,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {KeyRound,PlugZap,Trash2,X} from 'lucide-react';
import {api} from './api';
import {ErrorNotice,Loading} from './components';
import type {LlmStatus} from './types';

export default function LlmSettingsDialog({userId,onClose}:{userId:string;onClose:()=>void}){
 const client=useQueryClient(),queryKey=['llm-status',userId];
 const status=useQuery({queryKey,queryFn:()=>api<LlmStatus>('/llm/status'),retry:false});
 const [key,setKey]=useState(''),[pending,setPending]=useState(''),[error,setError]=useState<unknown>(null),[notice,setNotice]=useState('');
 const data=status.data,busy=!!pending,dirty=!!key.trim();
 async function perform(action:'save'|'check'|'delete'){
  if(busy)return;
  setPending(action);setError(null);setNotice('');
  try{
   const result=await api<LlmStatus>(action==='check'?'/llm/check':'/llm/credential',{
    method:action==='save'?'PUT':action==='check'?'POST':'DELETE',
    ...(action==='save'?{body:JSON.stringify({api_key:key})}:{})
   });
   client.setQueryData(queryKey,result);
   if(action!=='check'){setKey('');setNotice(action==='delete'?'API 키를 삭제했습니다.':'API 키를 저장했습니다.')}
   void client.invalidateQueries({queryKey:['bootstrap']});
  }catch(e){setError(e);void status.refetch()}finally{setPending('')}
 }
 const label=data?.status==='connected'?'연결 확인됨':data?.status==='error'?'연결 실패':data?.key_registered?'등록됨 · 연결 미확인':'미등록';
 return <Dialog open onClose={()=>{if(!busy)onClose()}} maxWidth="sm" fullWidth aria-labelledby="llm-settings-title" slotProps={{paper:{className:'llm-settings-dialog'}}}>
  <div className="dialog-heading"><div className="dialog-icon"><KeyRound size={21}/></div><div><h2 id="llm-settings-title">LLM API 키 설정</h2><p>내 계정의 LLM 연결</p></div><IconButton disabled={busy} onClick={onClose} aria-label="LLM 설정 닫기"><X size={19}/></IconButton></div>
  <DialogContent>
   {status.isPending?<Loading/>:<>
    <ErrorNotice error={status.error}/>
    {data&&<>
     <dl className="llm-server-details"><div><dt>Endpoint</dt><dd>{data.base_url||'미설정'}</dd></div><div><dt>모델</dt><dd>{data.model||'미설정'}</dd></div></dl>
     {!data.server_configured&&<Alert severity="info" sx={{mb:2}}>관리자가 서버의 LLM_BASE_URL과 LLM_MODEL을 설정해야 연결할 수 있습니다.</Alert>}
     <div className="llm-key-status"><span role="status"><i className={`llm-signal ${pending==='check'?'checking':data.status}`}/>{pending==='check'?'연결 확인 중…':label}</span><Button size="small" variant="outlined" startIcon={<PlugZap size={15}/>} disabled={busy||dirty||!data.configured} onClick={()=>perform('check')}>연결 확인</Button></div>
     {data.checked_at&&<p className="llm-check-time">최근 확인: {new Date(data.checked_at).toLocaleString()}</p>}
     {data.status==='error'&&data.message&&<Alert severity="error" sx={{mb:2}}>{data.message}</Alert>}
     <form id="llm-key-form" onSubmit={e=>{e.preventDefault();if(dirty)void perform('save')}} autoComplete="off">
      <TextField fullWidth variant="outlined" type="password" label={data.key_registered?'새 API 키':'API 키'} value={key}
       placeholder={data.key_registered?'변경할 키를 입력하세요':'발급받은 API 키를 입력하세요'} disabled={busy}
       autoComplete="new-password" slotProps={{htmlInput:{maxLength:8192,spellCheck:false,autoCapitalize:'none'}}}
       onChange={e=>{setKey(e.target.value);setNotice('');setError(null)}}/>
     </form>
     {dirty&&<p className="llm-key-hint">키를 저장한 후 연결을 확인하세요.</p>}
    </>}
   </>}
   <ErrorNotice error={error}/>
   {notice&&<Alert severity="success" sx={{mt:2}}>{notice}</Alert>}
  </DialogContent>
  <DialogActions>
   <Button color="error" startIcon={<Trash2 size={15}/>} disabled={busy||!data?.key_registered} onClick={()=>perform('delete')}>{pending==='delete'?'삭제 중…':'키 삭제'}</Button>
   <span className="llm-action-spacer"/>
   <Button disabled={busy} onClick={onClose}>닫기</Button>
   <Button variant="contained" type="submit" form="llm-key-form" disabled={busy||!data||!dirty}>{pending==='save'?'저장 중…':data?.key_registered?'수정 저장':'등록'}</Button>
  </DialogActions>
 </Dialog>;
}
