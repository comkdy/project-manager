import {useState} from 'react';
import {Autocomplete,Button,Chip,Dialog,DialogActions,DialogContent,DialogContentText,DialogTitle,FormControlLabel,IconButton,Switch,TextField,Tooltip} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Plus,Pencil,Trash2,FolderKanban} from 'lucide-react';
import {api} from './api';
import {ErrorNotice,Loading} from './components';
import type {Project} from './types';
import './project-management.css';

type ManagedProject=Project&{lot_cds:string[];description:string;active:boolean;version:number};
type Draft={id?:number;name:string;lot_cds:string[];description:string;active:boolean;version?:number};
export default function ProjectManagement({workspace,onChanged}:{workspace:string;onChanged:()=>void}){
 const client=useQueryClient();
 const projects=useQuery({queryKey:['admin','projects',workspace],queryFn:()=>api<ManagedProject[]>('/admin/projects')});
 const codes=useQuery({queryKey:['admin','lot-codes',workspace],queryFn:()=>api<{values:string[];truncated:boolean}>('/admin/project-lot-codes')});
 const [draft,setDraft]=useState<Draft|null>(null),[input,setInput]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 async function save(value:Draft){
  setBusy(true);setError(null);
  try{
   await api(`/admin/projects${value.id?`/${value.id}`:''}`,{method:value.id?'PUT':'POST',body:JSON.stringify({name:value.name,lot_cds:value.lot_cds,description:value.description,active:value.active,expected_version:value.version})});
   setDraft(null);setInput('');onChanged();
   await Promise.all([client.invalidateQueries({queryKey:['admin','projects']}),client.invalidateQueries({queryKey:['bootstrap']}),client.invalidateQueries({queryKey:['versions']}),client.invalidateQueries({queryKey:['chart']})]);
  }catch(e){setError(e)}finally{setBusy(false)}
 }
 const [removing,setRemoving]=useState<ManagedProject|null>(null);
 async function removeProject(){
  if(!removing)return;
  setBusy(true);setError(null);
  try{
   await api(`/admin/projects/${removing.id}?expected_version=${removing.version}`,{method:'DELETE'});
   setRemoving(null);onChanged();
   await Promise.all([client.invalidateQueries({queryKey:['admin','projects']}),client.invalidateQueries({queryKey:['bootstrap']}),client.invalidateQueries({queryKey:['versions']}),client.invalidateQueries({queryKey:['chart']})]);
  }catch(e){setError(e)}finally{setBusy(false)}
 }
 const mergedCodes=draft?[...new Set([...draft.lot_cds,...input.split(/[,;\n]/)].map(x=>x.trim()).filter(Boolean))]:[];
 return <section className="project-management"><div className="project-management-toolbar"><div><h3>프로젝트 관리</h3><p>현재 DB의 프로젝트와 포함할 lot_cd를 관리합니다.</p></div><Button variant="contained" startIcon={<Plus size={17}/>} onClick={()=>{setError(null);setInput('');setDraft({name:'',lot_cds:[],description:'',active:true})}}>프로젝트 추가</Button></div>
  {!draft&&!removing&&<ErrorNotice error={error}/>}<ErrorNotice error={projects.error}/>
  {projects.isPending?<Loading/>:!projects.data?.length?<div className="project-management-empty"><FolderKanban size={28}/><p>프로젝트를 추가하고 lot_cd를 연결하세요.</p></div>:<div className="project-management-list">{projects.data.map(project=><article key={project.id} className={!project.active?'inactive':''}><div className="project-management-info"><strong title={project.name}>{project.name}</strong><div>{project.lot_cds.map(code=><Chip key={code} label={code} size="small"/>)}</div>{project.description&&<p title={project.description}>{project.description}</p>}</div><div className="project-management-actions"><FormControlLabel label={project.active?'활성':'비활성'} control={<Switch size="small" checked={project.active} disabled={busy} slotProps={{input:{'aria-label':`${project.name} 활성`}}} onChange={(_,active)=>void save({...project,active})}/>}/><Tooltip title="프로젝트 수정"><IconButton aria-label={`${project.name} 수정`} disabled={busy} onClick={()=>{setError(null);setInput('');setDraft({...project})}}><Pencil size={17}/></IconButton></Tooltip><Tooltip title="프로젝트 삭제"><IconButton aria-label={`${project.name} 삭제`} disabled={busy} className="project-delete-button" onClick={()=>{setError(null);setRemoving(project)}}><Trash2 size={17}/></IconButton></Tooltip></div></article>)}</div>}
  <Dialog open={!!draft} onClose={()=>!busy&&setDraft(null)} fullWidth maxWidth="sm"><DialogTitle>{draft?.id?'프로젝트 수정':'프로젝트 추가'}</DialogTitle>{draft&&<form onSubmit={e=>{e.preventDefault();void save({...draft,lot_cds:mergedCodes})}}><DialogContent className="project-management-fields"><ErrorNotice error={error}/><TextField autoFocus fullWidth required label="프로젝트 명" value={draft.name} inputProps={{maxLength:200}} onChange={e=>setDraft({...draft,name:e.target.value})}/><Autocomplete multiple freeSolo options={codes.data?.values??[]} value={draft.lot_cds} inputValue={input} onInputChange={(_,value)=>setInput(value)} onChange={(_,value)=>setDraft({...draft,lot_cds:value})} renderInput={params=><TextField {...params} label="포함하는 lot_cd" placeholder="선택 또는 입력 후 Enter"/>}/><TextField fullWidth multiline minRows={3} label="설명" value={draft.description} inputProps={{maxLength:4000}} onChange={e=>setDraft({...draft,description:e.target.value})}/><FormControlLabel label="프로젝트 활성" control={<Switch checked={draft.active} onChange={(_,active)=>setDraft({...draft,active})}/>}/></DialogContent><DialogActions><Button onClick={()=>setDraft(null)} disabled={busy}>취소</Button><Button type="submit" variant="contained" disabled={busy||!draft.name.trim()||!mergedCodes.length}>{busy?'저장 중…':'저장'}</Button></DialogActions></form>}</Dialog>
  <Dialog open={!!removing} onClose={()=>!busy&&setRemoving(null)} fullWidth maxWidth="xs" aria-labelledby="project-delete-title"><DialogTitle id="project-delete-title">프로젝트 삭제</DialogTitle><DialogContent><ErrorNotice error={error}/><DialogContentText><strong>{removing?.name}</strong> 프로젝트를 앱에서 삭제할까요?</DialogContentText><DialogContentText sx={{mt:1}}>원천 DB의 테이블과 데이터는 변경하지 않습니다. 이 프로젝트의 데이터셋·카드 설정은 보관되며 화면에서 숨겨집니다.</DialogContentText></DialogContent><DialogActions><Button autoFocus disabled={busy} onClick={()=>setRemoving(null)}>취소</Button><Button color="error" variant="contained" disabled={busy} onClick={()=>void removeProject()}>{busy?'삭제 중…':'프로젝트 삭제'}</Button></DialogActions></Dialog>
 </section>
}
