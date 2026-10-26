import {DatasetLabel} from './DatasetScopeBadge';
import {useEffect,useState} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,FormControlLabel,IconButton,MenuItem,Switch,TextField} from '@mui/material';
import {ChartGantt,Braces,X} from 'lucide-react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import DatasetEditor from './DatasetEditor';
import {api,post} from './api';
import {ErrorNotice,FilterBuilder,Loading} from './components';
import {GanttView} from './GanttChart';
import {CommonPeriodControl} from './PeriodControls';
import type {Bootstrap,CardSpec,ChartResult,Dataset,Filter,GanttSettings,QueryContext} from './types';
import type {CardDraftEditorProps} from './cardDraftEditor';
import {ganttDraftSource} from './editorDraftValues';

const joined=(d?:Dataset)=>!!d&&['current_operation','current_started_at','completed_at','history_count','expected_hours','tat_samples','tat_basis','remark'].every(n=>d.columns.some(c=>c.name===n));
const defaultLines=(d:Dataset):Filter[]=>[
 {column:'area_id',op:'not_in' as const,value:['VIRTUAL','CNC']},
 {column:'samp_seq',op:'eq' as const,value:0},
 {column:'main_oper_yn',op:'eq' as const,value:d.columns.find(c=>c.name==='main_oper_yn')?.type==='boolean'?true:'Y'},
].filter(f=>d.columns.some(c=>c.name===f.column));

export default function GanttEditor({initial,datasets,context,onClose,onSave,onOther,draftNotice,onEdited,onDraftChange,onBusyChange}:{initial:CardSpec;datasets:Dataset[];context:QueryContext;onClose:()=>void;onSave:(card:CardSpec)=>Promise<void>;onOther:(card:CardSpec)=>void}&CardDraftEditorProps){
 const client=useQueryClient();
 const [edited,setEdited]=useState<Dataset|null>(null),[queryOpen,setQueryOpen]=useState(false),[queryVersion,setQueryVersion]=useState<number|null>(null);
 const planDatasets=[...datasets.filter(d=>d.id!==edited?.id),...(edited?[edited]:[])].filter(d=>['lot_cd','lot_id','operation_seq','operation'].every(n=>d.columns.some(c=>c.name===n))).sort((a,b)=>Number(joined(b))-Number(joined(a)));
 const [draft,setDraft]=useState<CardSpec>(()=>({...initial,kind:'gantt',title:initial.kind==='gantt'&&initial.title!=='새 시각화 카드'?initial.title:'랏 일정',aggregate:'none',x:undefined,y:undefined,color:undefined,trellis:undefined,value_expression:undefined,filters:initial.filters.filter(f=>['lot_cd','lot_id'].includes(f.column)),...ganttDraftSource(initial,planDatasets),period:initial.period??{use_dashboard:true}}));
 const catalog=useQuery({queryKey:['gantt-catalog',context.project_id],queryFn:()=>api<Bootstrap>(`/bootstrap${context.project_id!=null?`?project_id=${context.project_id}`:''}`)});
 const roles=[{key:'status_source_id',label:'현재 상태',columns:['lot_cd','lot_id','operation','remark'],table:'current_status'},{key:'tat_source_id',label:'공정 소요 시간 · hour',columns:['lot_cd','lot_id','operation','tat'],table:'operation_tat'},{key:'history_source_id',label:'완료 이력',columns:['lot_cd','lot_id','operation','end_dt_tm'],table:'lot_his'}] as const;
 useEffect(()=>{if(catalog.data&&!draft.gantt){const d=planDatasets.find(d=>d.id===draft.dataset_id);const defaults=joined(d)?{data_mode:'dataset',line_filters:defaultLines(d!),key_filters:[]}:Object.fromEntries(roles.map(role=>[role.key,catalog.data!.catalog.find(s=>s.table_name===role.table&&role.columns.every(c=>s.columns.some(v=>v.name===c)))?.id??null]));setDraft(v=>({...v,gantt:defaults as GanttSettings}))}},[catalog.data,draft.gantt,draft.dataset_id]);
 useEffect(()=>{onDraftChange?.({card:draft,expressionDraft:''})},[draft,onDraftChange]);
 const patch=(value:Partial<CardSpec>,internal=false)=>{if((saving||creating)&&!internal)return;onEdited?.();setDraft(v=>({...v,...value}))};
 const patchGantt=(value:Partial<GanttSettings>)=>{if(saving||creating)return;onEdited?.();setDraft(v=>({...v,gantt:{...v.gantt!,...value}}))};
 const [saving,setSaving]=useState(false),[creating,setCreating]=useState(false),[error,setError]=useState<unknown>(null);
 useEffect(()=>{onBusyChange?.(saving||creating)},[saving,creating,onBusyChange]);
 const datasetMode=draft.gantt?.data_mode==='dataset';
 const complete=planDatasets.some(dataset=>dataset.id===draft.dataset_id)&&!!draft.gantt&&(datasetMode||roles.every(r=>!!draft.gantt?.[r.key]));
 const json=JSON.stringify(draft),[settled,setSettled]=useState(json);
 useEffect(()=>{const timer=setTimeout(()=>setSettled(json),350);return()=>clearTimeout(timer)},[json]);
 const preview=useQuery({queryKey:['gantt-preview',settled,context],queryFn:({signal})=>api<ChartResult>('/cards/query',{method:'POST',body:JSON.stringify({card:JSON.parse(settled),context}),signal}),enabled:complete&&json===settled});
 const ready=complete&&json===settled&&preview.isSuccess&&!preview.isFetching;
 const period=draft.period??{use_dashboard:true};
 const planDataset=planDatasets.find(d=>d.id===draft.dataset_id);
 const versions=useQuery({queryKey:['versions',context.project_id??null,draft.dataset_id],queryFn:()=>api<Dataset[]>(`/datasets/${draft.dataset_id}/versions${context.project_id!=null?`?project_id=${context.project_id}`:''}`),enabled:queryOpen&&!!planDataset});
 const queryDataset=versions.data?.find(d=>d.version===(queryVersion??planDataset?.version))??planDataset;
 async function savedDataset(saved:Dataset){
  setEdited(saved);setQueryVersion(null);patch({dataset_id:saved.id,dataset_version:saved.version});setQueryOpen(false);
  await Promise.all(['bootstrap','versions','table','dashboard','chart','gantt-preview'].map(key=>client.invalidateQueries({queryKey:[key]})));
 }
 const statusSource=catalog.data?.catalog.find(s=>s.id===draft.gantt?.status_source_id);
 async function createJoinedDataset(){
  setCreating(true);setError(null);
  try{
   const template=await post<Pick<Dataset,'name'|'spec'|'project_id'>>('/gantt/dataset-template',{card:draft,context});
   const saved=await post<Dataset>('/datasets',template);
   setEdited(saved);setQueryVersion(null);
   patch({dataset_id:saved.id,dataset_version:saved.version,gantt:{data_mode:'dataset',as_of:draft.gantt?.as_of,line_filters:defaultLines(saved),key_filters:[]}},true);
   await client.invalidateQueries({queryKey:['bootstrap']});
  }catch(e){setError(e)}finally{setCreating(false)}
 }
 async function save(){setSaving(true);setError(null);try{await post('/cards/query',{card:draft,context});await onSave(draft);onClose()}catch(e){setError(e)}finally{setSaving(false)}}
 return <><Dialog open fullWidth maxWidth="lg" onClose={saving||creating?undefined:onClose} aria-labelledby="gantt-editor-title" PaperProps={{className:'visual-card-dialog'}}><div className="dialog-heading"><div className="dialog-icon"><ChartGantt size={21}/></div><h2 id="gantt-editor-title">간트차트 구성</h2><IconButton disabled={creating||saving} onClick={onClose} aria-label="간트차트 구성 닫기"><X/></IconButton></div>{draftNotice&&<div style={{padding:'0 24px'}}>{draftNotice}</div>}<DialogContent className="gantt-editor-content"><fieldset disabled={saving||creating} style={{display:'contents'}}><aside className="gantt-editor-settings"><TextField label="카드 제목" value={draft.title} onChange={e=>patch({title:e.target.value})}/><TextField select disabled={creating||saving} label="일정 데이터셋" value={draft.dataset_id} onChange={e=>{const d=planDatasets.find(d=>d.id===e.target.value);if(d)patch({dataset_id:d.id,dataset_version:d.version,gantt:joined(d)?{data_mode:'dataset',as_of:draft.gantt?.as_of,line_filters:defaultLines(d),key_filters:[]}:undefined})}}>{draft.dataset_id&&!planDatasets.some(d=>d.id===draft.dataset_id)&&<MenuItem value={draft.dataset_id} disabled>{draft.dataset_id} · 일정 데이터셋을 다시 선택해 주세요</MenuItem>}{planDatasets.map(d=><MenuItem key={d.id} value={d.id}><DatasetLabel dataset={d}/></MenuItem>)}</TextField>{draft.dataset_id&&!planDatasets.some(d=>d.id===draft.dataset_id)&&<Alert severity="warning">편집 중인 일정 데이터셋이 없거나 필요한 컬럼이 변경되었습니다. 원래 선택은 유지했으며 다른 데이터셋을 자동 적용하지 않았습니다.</Alert>}{!planDatasets.length&&<Alert severity="info">lot_cd, lot_id, operation_seq, operation이 있는 데이터셋이 필요합니다.</Alert>}
  <Button startIcon={<Braces size={16}/>} disabled={!planDataset||!catalog.data||creating} onClick={()=>{setQueryVersion(null);setQueryOpen(true)}}>{datasetMode?'일정 쿼리 관리':'계획 쿼리 관리'}</Button>
  {!datasetMode&&<>{roles.map(role=><TextField key={role.key} select disabled={creating||saving} label={role.label} value={draft.gantt?.[role.key]??''} onChange={e=>patchGantt({[role.key]:e.target.value||null,...(role.key==='status_source_id'?{started_column:null}:{})})}><MenuItem value="">테이블 선택</MenuItem>{catalog.data?.catalog.filter(s=>role.columns.every(c=>s.columns.some(v=>v.name===c))).map(s=><MenuItem key={s.id} value={s.id}>{s.schema_name}.{s.table_name}</MenuItem>)}</TextField>)}<Button variant="outlined" disabled={!complete||creating} onClick={()=>void createJoinedDataset()}>{creating?'만드는 중…':'조인 SQL 데이터셋 만들기'}</Button></>}
  {datasetMode&&<>
   <small className="gantt-filter-note">조인·컬럼·TAT 계산은 일정 쿼리에서 수정합니다. 아래 필터는 표시만 바꾸며 숨겨진 공정의 시간도 유지합니다.</small>
   <details className="gantt-presentation-filter"><summary>시작선 표시 필터 <span>{draft.gantt?.line_filters?.length??0}</span></summary><div><small className="gantt-filter-note">모든 조건을 만족하는 공정에 선을 표시합니다. 조건이 없으면 모든 공정에 표시합니다.</small><FilterBuilder columns={planDataset?.columns??[]} filters={draft.gantt?.line_filters??[]} onChange={line_filters=>patchGantt({line_filters})}/></div></details>
   <details className="gantt-presentation-filter"><summary>핵심 공정 필터 <span>{draft.gantt?.key_filters?.length??0}</span></summary><div><small className="gantt-filter-note">모든 조건을 만족하는 공정에 깃발과 공정명을 표시합니다. 시작선 필터와 별개이며, 조건이 없으면 표시하지 않습니다.</small><FilterBuilder columns={planDataset?.columns??[]} filters={draft.gantt?.key_filters??[]} onChange={key_filters=>patchGantt({key_filters})}/></div></details>
  </>}
  <details><summary>세부 설정 · 필터</summary><div>{!datasetMode&&<TextField select label="현재 공정 시작 시각" value={draft.gantt?.started_column??''} onChange={e=>patchGantt({started_column:e.target.value||null})}><MenuItem value="">이전 공정 완료로 추정</MenuItem>{statusSource?.columns.filter(c=>/timestamp|date/i.test(c.type)).map(c=><MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}</TextField>}<FormControlLabel label="고정 시점으로 보기" control={<Switch checked={!!draft.gantt?.as_of} onChange={(_,checked)=>patchGantt({as_of:checked?new Date().toISOString():null})}/>}/>{draft.gantt?.as_of&&<TextField label="기준 시각" type="datetime-local" InputLabelProps={{shrink:true}} value={new Date(new Date(draft.gantt.as_of).getTime()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16)} onChange={e=>{if(e.target.value)patchGantt({as_of:new Date(e.target.value).toISOString()})}}/>}<CommonPeriodControl period={period} context={context} onChange={period=>patch({period})}/>
   <FilterBuilder columns={[{name:'lot_cd',type:'text'},{name:'lot_id',type:'text'}]} filters={draft.filters} onChange={filters=>patch({filters})}/></div></details><Button color="secondary" onClick={()=>onOther(draft)}>일반 차트로 변경</Button><ErrorNotice error={catalog.error}/><ErrorNotice error={error}/>
 </aside><section className="gantt-editor-preview"><h3>{draft.title}</h3>{!complete?<Alert severity="info">일정에 사용할 데이터셋과 테이블을 선택하세요.</Alert>:json!==settled||preview.isPending?<Loading label="일정 미리보기"/>:preview.error?<ErrorNotice error={preview.error}/>:preview.data&&<GanttView result={preview.data}/>}</section></fieldset></DialogContent><DialogActions><Button disabled={saving||creating} onClick={onClose}>취소</Button><Button variant="contained" disabled={saving||creating||!datasetMode||!ready||!draft.title.trim()} onClick={()=>void save()}>{saving?'저장 중…':'카드 저장'}</Button></DialogActions></Dialog>{queryOpen&&queryDataset&&catalog.data&&<DatasetEditor key={`${queryDataset.id}:${queryDataset.version}`} dataset={queryDataset} versions={versions.data} versionsError={versions.error} onSelectVersion={setQueryVersion} catalog={catalog.data.catalog} context={context} onClose={()=>setQueryOpen(false)} onSaved={savedDataset}/>}</>;
}
