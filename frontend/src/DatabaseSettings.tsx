import {useEffect,useState} from 'react';
import {Button,Chip,Dialog,DialogActions,DialogContent,DialogContentText,DialogTitle,IconButton,Switch,TextField,Tooltip} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Database,Plus,Power,RefreshCw,Settings2,Trash2} from 'lucide-react';
import {api} from './api';
import {databaseStatusSignal} from './databaseStatus';
import {ErrorNotice,Loading} from './components';
import type {Column,DatabaseStatus} from './types';
import DatabaseConnectionDialog,{ConnectionSignal} from './DatabaseConnectionDialog';
import type {ConnectionState,DatabaseProfile,ConnectionResult} from './DatabaseConnectionDialog';
export {ConnectionSignal} from './DatabaseConnectionDialog';

interface Profiles {profiles:DatabaseProfile[];active_id:string|null;workspace_token:string}
interface Table {schema_name:string;table_name:string;columns:Column[];visible:boolean}

export default function DatabaseSettings({onWorkspaceChanged}:{onWorkspaceChanged:(token:string)=>Promise<void>}){
 const client=useQueryClient();
 const profiles=useQuery({queryKey:['admin','databases'],queryFn:()=>api<Profiles>('/admin/databases')});
 const status=useQuery({queryKey:['database-status'],queryFn:()=>api<DatabaseStatus>('/database-status'),refetchInterval:30000,retry:false});
 const [selected,setSelected]=useState<string|null|undefined>(undefined);
 const [connectionDialog,setConnectionDialog]=useState<DatabaseProfile|null|undefined>(undefined);
 const [tableBusy,setTableBusy]=useState(false),[tableError,setTableError]=useState<unknown>(null),[search,setSearch]=useState('');
 const [activating,setActivating]=useState(false),[activationError,setActivationError]=useState<unknown>(null);
 const [deleteTarget,setDeleteTarget]=useState<DatabaseProfile|null>(null),[removing,setRemoving]=useState(false),[deleteError,setDeleteError]=useState<unknown>(null);
 const busy=tableBusy||activating||removing;
 const tables=useQuery({queryKey:['admin','database-tables',selected],queryFn:()=>api<{tables:Table[]}>(`/admin/databases/${selected}/tables`),enabled:!!selected,retry:false});
 function choose(profile?:DatabaseProfile){setSelected(profile?.id??null);setTableError(null);setActivationError(null);setSearch('')}
 useEffect(()=>{if(selected===undefined&&profiles.data)choose(profiles.data.profiles.find(p=>p.id===profiles.data.active_id)??profiles.data.profiles[0])},[profiles.data,selected]);
 async function saved(result:ConnectionResult){
  setSelected(result.profile_id);setSearch('');setTableError(null);setActivationError(null);
  if(result.active)await onWorkspaceChanged(result.workspace_token);
  await client.invalidateQueries({queryKey:['admin','databases']});
  await client.invalidateQueries({queryKey:['admin','database-tables']});
 }
 async function activate(){
  if(!selected||activating)return;
  setActivating(true);setActivationError(null);
  try{
   const result=await api<ConnectionResult>(`/admin/databases/${selected}/activate`,{method:'POST'});
   await saved(result);
  }catch(e){setActivationError(e)}finally{setActivating(false)}
 }
 function closeDelete(){if(!removing){setDeleteTarget(null);setDeleteError(null)}}
 async function removeRegistration(){
  if(!deleteTarget||removing)return;
  const id=deleteTarget.id;
  setRemoving(true);setDeleteError(null);
  try{
   await api(`/admin/databases/${id}`,{method:'DELETE'});
   await client.cancelQueries({queryKey:['admin','database-tables',id]});
   client.removeQueries({queryKey:['admin','database-tables',id]});
   client.setQueryData<Profiles>(['admin','databases'],old=>old?{...old,profiles:old.profiles.filter(p=>p.id!==id)}:old);
   const remaining=client.getQueryData<Profiles>(['admin','databases']);
   choose(remaining?.profiles.find(p=>p.id===remaining.active_id)??remaining?.profiles[0]);
   setDeleteTarget(null);
   await client.invalidateQueries({queryKey:['admin','databases']});
   await client.invalidateQueries({queryKey:['admin','audit']});
  }catch(e){setDeleteError(e)}finally{setRemoving(false)}
 }
 async function toggleTable(table:Table){
  setTableBusy(true);setTableError(null);
  try{
   const result=await api<{active:boolean;workspace_token:string}>(`/admin/databases/${selected}/table-visibility`,{
    method:'PUT',body:JSON.stringify({schema_name:table.schema_name,table_name:table.table_name,visible:!table.visible})});
   await client.invalidateQueries({queryKey:['admin','database-tables',selected]});
   if(result.active)await onWorkspaceChanged(result.workspace_token);
  }catch(e){setTableError(e)}finally{setTableBusy(false)}
 }
 const active=!!selected&&selected===profiles.data?.active_id;
 const currentSignal:ConnectionState=activating?{state:'checking',label:'DB 활성화 중'}:active?databaseStatusSignal(status,true):
  selected&&tables.isError?{state:'error',label:'연결 오류'}:
  selected&&tables.isFetching?{state:'checking',label:'연결 확인 중'}:
  selected&&tables.isSuccess?{state:'connected',label:'접속 가능 · 비활성 DB'}:{state:'idle',label:'연결 전'};
 const selectedProfile=profiles.data?.profiles.find(p=>p.id===selected);
 const shown=(tables.data?.tables??[]).filter(t=>`${t.schema_name}.${t.table_name}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
 return <div className="db-settings">
  <div className="db-section-heading"><h3>PostgreSQL</h3><Button startIcon={<Plus size={16}/>} onClick={()=>setConnectionDialog(null)} disabled={busy}>DB 추가</Button></div>
  <p className="db-activation-hint">앱에서 사용할 DB는 하나만 활성화할 수 있습니다.</p>
  <ErrorNotice error={profiles.error}/>
  {profiles.isPending?<Loading/>:<div className="db-profile-list">{profiles.data?.profiles.map(p=><button type="button" key={p.id} aria-pressed={selected===p.id} className={`db-profile ${selected===p.id?'selected':''}`} onClick={()=>choose(p)} disabled={busy}>
   <Database size={20}/><span><strong>{p.database}</strong><small>{p.host}:{p.port}</small></span>{p.id===profiles.data.active_id&&<Chip size="small" label="활성" variant="outlined"/>}
  </button>)}</div>}
  {selectedProfile&&<div className="db-connection-summary"><ConnectionSignal {...currentSignal}/><div className="db-connection-actions">
   <Button startIcon={<Settings2 size={15}/>} onClick={()=>setConnectionDialog(selectedProfile)} disabled={busy}>접속 정보</Button>
   <Button variant="contained" startIcon={<Power size={15}/>} onClick={()=>void activate()} disabled={busy||active}>{activating?'활성화 중…':active?'활성 DB':'이 DB 활성화'}</Button>
   <Tooltip title={active?'다른 DB를 활성화한 뒤 등록을 삭제할 수 있습니다.':'등록 삭제'}>
    <span><IconButton color="error" aria-label="DB 등록 삭제" disabled={busy||active} onClick={()=>{setDeleteTarget(selectedProfile);setDeleteError(null)}}><Trash2 size={17}/></IconButton></span>
   </Tooltip>
  </div></div>}
  <ErrorNotice error={activationError}/>
  <Dialog open={!!deleteTarget} onClose={closeDelete} disableEscapeKeyDown={removing} fullWidth maxWidth="xs"
   aria-labelledby="db-registration-delete-title" aria-describedby="db-registration-delete-description">
   <DialogTitle id="db-registration-delete-title">DB 등록을 삭제할까요?</DialogTitle>
   <DialogContent>
    <div className="db-delete-target"><strong>{deleteTarget?.database}</strong><small>{deleteTarget?.host}:{deleteTarget?.port}</small></div>
    <DialogContentText id="db-registration-delete-description">앱에 저장된 접속 정보만 삭제합니다. 실제 DB와 데이터, 대시보드·데이터셋·쿼리는 유지됩니다.</DialogContentText>
    <ErrorNotice error={deleteError}/>
   </DialogContent>
   <DialogActions sx={{px:3,pb:2}}><Button onClick={closeDelete} disabled={removing}>취소</Button><Button color="error" variant="contained" onClick={()=>void removeRegistration()} disabled={removing}>{removing?'삭제 중…':'등록 삭제'}</Button></DialogActions>
  </Dialog>
  {connectionDialog!==undefined&&<DatabaseConnectionDialog profile={connectionDialog}
   status={connectionDialog?currentSignal:{state:'idle',label:'연결 전'}} onClose={()=>setConnectionDialog(undefined)}
   onSaved={saved} onChecked={()=>client.invalidateQueries({queryKey:['database-status']})}/>}
  {selected&&<section className="db-tables">
   <div className="db-section-heading"><h3>테이블 <span>{tables.data?.tables.length??''}</span></h3><Button startIcon={<RefreshCw size={14}/>} disabled={tables.isFetching||busy} onClick={()=>void tables.refetch()}>새로고침</Button></div>
   <ErrorNotice error={tableError||tables.error}/>
   {tables.isPending?<Loading label="테이블 불러오는 중"/>:tables.data&&<>
    {tables.data.tables.length>10&&<TextField size="small" fullWidth label="테이블 검색" value={search} onChange={e=>setSearch(e.target.value)} sx={{mb:1.5}}/>}
    <div className="db-table-heading"><span>테이블명 / 컬럼</span><span>앱에 표시</span></div>
    <div className="db-table-list">{shown.map(t=><div className="db-table-row" key={JSON.stringify([t.schema_name,t.table_name])}>
     <details><summary><code>{t.schema_name}.{t.table_name}</code><small>{t.columns.length} columns</small></summary><div className="db-column-list">{t.columns.map(c=><span key={c.name}><code>{c.name}</code><small>{c.type}</small></span>)}</div></details>
     <Switch size="small" checked={t.visible} disabled={busy} onChange={()=>void toggleTable(t)} slotProps={{input:{'aria-label':`${t.schema_name}.${t.table_name} 앱에 표시`}}}/>
    </div>)}</div>{!shown.length&&<p className="db-empty">{search?'검색 결과가 없습니다.':'조회 가능한 테이블이 없습니다.'}</p>}
   </>}
  </section>}
 </div>
}
