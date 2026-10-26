import {useState} from 'react';
import {Alert,Button,Chip,FormControlLabel,MenuItem,Switch,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Plus,ShieldCheck} from 'lucide-react';
import {api,ApiError} from './api';
import type {Role,User} from './types';
import {ErrorNotice,Loading,SectionLabel} from './components';

const roles:[Role,string][]=[['viewer','조회자'],['editor','편집자'],['admin','관리자']];

export default function UserManagement({currentUser}:{currentUser:User}){
 const client=useQueryClient();
 const users=useQuery({queryKey:['admin','users'],queryFn:()=>api<User[]>('/admin/users')});
 const [account,setAccount]=useState({employee_id:'',role:'viewer' as Role,active:true});
 const [error,setError]=useState<unknown>(null),[success,setSuccess]=useState(''),[busy,setBusy]=useState(false);

 async function save(path:string,value:unknown,method:'POST'|'PATCH'){
  setBusy(true);setError(null);setSuccess('');
  try{
   await api(path,{method,body:JSON.stringify(value)});
   await client.invalidateQueries({queryKey:['admin','users']});
   await client.invalidateQueries({queryKey:['admin','audit']});
   setSuccess('사용자 권한을 저장했습니다.');
   return true;
  }catch(e){
   if(e instanceof ApiError&&e.status===409){
    await users.refetch();
    setError(new Error(`${e.message} 최신 사용자 목록을 불러왔습니다.`));
   }else setError(e);
   return false;
  }finally{setBusy(false)}
 }

 return <>
  <Alert severity="info" icon={<ShieldCheck size={20}/>} sx={{mb:2}}>로그인과 이름·소속 정보는 사내 SSO 및 디렉터리에서 관리합니다. 여기서는 사번별 Project Manager 접근 권한과 활성 상태를 관리합니다.</Alert>
  <ErrorNotice error={error}/>{success&&<Alert severity="success" sx={{mb:2}}>{success}</Alert>}
  <SectionLabel>사번으로 권한 사전 등록</SectionLabel>
  <form className="admin-form user-create-form" onSubmit={async e=>{
   e.preventDefault();
   if(await save('/admin/users',{...account,employee_id:account.employee_id.trim()},'POST'))setAccount({employee_id:'',role:'viewer',active:true});
  }}>
   <TextField required label="사번" value={account.employee_id} inputProps={{maxLength:64}} onChange={e=>setAccount(a=>({...a,employee_id:e.target.value}))} disabled={busy}/>
   <TextField select label="권한" value={account.role} onChange={e=>setAccount(a=>({...a,role:e.target.value as Role}))} disabled={busy}>{roles.map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>
   <FormControlLabel control={<Switch checked={account.active} onChange={e=>setAccount(a=>({...a,active:e.target.checked}))} disabled={busy}/>} label="활성"/>
   <Button type="submit" variant="contained" disabled={busy||!account.employee_id.trim()} startIcon={<Plus size={16}/>}>권한 등록</Button>
  </form>
  <SectionLabel>팀 구성원</SectionLabel>
  <p className="user-management-note">SSO 계정의 이름과 소속은 로그인 시 갱신됩니다. 현재 관리자 자신의 권한과 활성 상태는 변경할 수 없습니다.</p>
  <ErrorNotice error={users.error}/>{users.isPending?<Loading/>:<div className="user-list">
   {users.data?.map(u=>{
    const displayName=u.name||u.employee_id||u.username;
    const editable=u.profile_source==='sso'&&u.id!==currentUser.id;
    return <div className={`user-row ${u.active?'':'user-inactive'}`} key={u.id}>
     <span className="user-avatar">{displayName.slice(0,1).toUpperCase()}</span>
     <div className="user-identity"><strong>{displayName}</strong><span>{u.employee_id?`사번 ${u.employee_id}`:u.username} · {u.team||(u.profile_source==='sso'?'SSO 로그인 후 소속 동기화':'소속 정보 없음')}</span></div>
     <Chip size="small" variant="outlined" label={u.profile_source==='sso'?'사내 SSO':u.profile_source==='local'?'테스트 계정':'이전 로컬 계정'}/>
     {u.id===currentUser.id&&<Chip size="small" label="현재 사용자"/>}
     <TextField select value={u.role} slotProps={{htmlInput:{'aria-label':`${displayName} 권한`}}} onChange={e=>save(`/admin/users/${u.id}`,{role:e.target.value,expected_version:u.version},'PATCH')} disabled={busy||!editable}>{roles.map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>
     <FormControlLabel className="user-active-control" control={<Switch size="small" checked={u.active} disabled={busy||!editable} inputProps={{'aria-label':`${displayName} 활성 상태`}} onChange={e=>save(`/admin/users/${u.id}`,{active:e.target.checked,expected_version:u.version},'PATCH')}/>} label={u.active?'활성':'비활성'}/>
    </div>
   })}
  </div>}
 </>
}
