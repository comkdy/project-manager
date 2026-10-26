import {useRef,useState} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,IconButton,MenuItem,Tab,Tabs,TextField} from '@mui/material';
import {Play,Save,X} from 'lucide-react';
import {useQuery} from '@tanstack/react-query';
import {api,ApiError,post} from './api';
import type {CardSpec,Dashboard,Dataset,DatasetSql,Proposal,QueryContext,Source} from './types';
import {DataGrid,ErrorNotice,FilterBuilder,Loading,SectionLabel} from './components';
import {Plot} from './Charts';
import {proposalDefinition,proposalSqlSpec,proposalTitle} from './chatProposal';
import PivotEditor from './PivotEditor';
import {chartPeriodDescription} from './dashboardPeriod';
import {pivotIssue} from './pivotSettings';
import ChatKnowledgePanel from './ChatKnowledgePanel';

export default function AssistantProposalDialog({initial,context,datasets,catalog,onClose,onApplied}:{initial:Proposal;context:QueryContext;datasets:Dataset[];catalog:Source[];onClose:()=>void;onApplied:()=>void}){
 const [draft,setDraft]=useState(()=>proposalDefinition(initial)),[preview,setPreview]=useState<Proposal|null>(initial);
 const [verified,setVerified]=useState(()=>JSON.stringify({draft:proposalDefinition(initial),period_start:initial.period_start??null})),[error,setError]=useState<unknown>(null),[busy,setBusy]=useState(false);
 const request=useRef({body:'',id:initial.proposal_id??crypto.randomUUID()});
 const project=initial.project_id??null;
 const source=catalog.find(s=>s.id===draft.dataset?.spec.source_id);
 const [editorMode,setEditorMode]=useState<'form'|'sql'>(initial.dataset?.spec.pivot&&source?'form':'sql');
 const pivot=draft.dataset?.spec.pivot;
 const pivotError=pivot?pivotIssue(pivot,source):null;
 const board=useQuery({queryKey:['assistant-board',initial.proposal_id,project],queryFn:()=>api<Dashboard>(`/dashboards${project!==null?`?project_id=${project}`:''}`),enabled:!!draft.card,refetchOnWindowFocus:false});
 const latest=useQuery({queryKey:['versions',project,draft.card?.dataset_id,'latest'],queryFn:()=>api<Dataset>(`/datasets/${encodeURIComponent(draft.card!.dataset_id)}/latest${project!==null?`?project_id=${project}`:''}`),enabled:!!draft.card&&!draft.dataset});
 const columns=preview?.query_result?.columns??latest.data?.columns??datasets.find(d=>d.id===draft.card?.dataset_id)?.columns??[];
 const signature=JSON.stringify({draft,period_start:context.period_start??null}),valid=verified===signature&&!!preview&&(preview.knowledge??initial.knowledge)?.availability!=='unavailable'&&!(preview.knowledge??initial.knowledge)?.blocking;
 function patchDataset(value:Partial<NonNullable<Proposal['dataset']>>){setDraft(p=>({...p,dataset:{...p.dataset!,...value}}))}
 function patchCard(value:Partial<CardSpec>){setDraft(p=>({...p,card:{...p.card!,...value}}))}
 async function compileDraft(){
  if(!draft.dataset?.spec.pivot)return draft;
  const result=await post<DatasetSql>('/datasets/sql',{spec:draft.dataset.spec,context:{project_id:project}});
  return {...draft,dataset:{...draft.dataset,spec:result.spec}};
 }
 async function switchEditor(next:'form'|'sql'){
  if(next===editorMode||pivotError)return;
  setBusy(true);setError(null);
  try{setDraft(await compileDraft());setEditorMode(next)}catch(e){setError(e)}finally{setBusy(false)}
 }
 async function runPreview(){
  if(pivotError)return;
  setBusy(true);setError(null);
  try{const compiled=await compileDraft();const result=await post<Proposal>('/chat/preview',{proposal:compiled,project_id:project,period_start:context.period_start,workspace_token:initial.workspace_token});const checked=proposalDefinition(result);setDraft(checked);setPreview(result);setVerified(JSON.stringify({draft:checked,period_start:context.period_start??null}))}
  catch(e){setError(e);setVerified('')}finally{setBusy(false)}
 }
 async function save(){
  if(!valid)return;
  setBusy(true);setError(null);
  const body={proposal:draft,project_id:project,period_start:context.period_start,workspace_token:initial.workspace_token,...(draft.card?{dashboard_id:board.data?.id,dashboard_version:board.data?.version}:{})};
  const fingerprint=JSON.stringify(body);
  if(request.current.body&&request.current.body!==fingerprint)request.current.id=crypto.randomUUID();
  request.current.body=fingerprint;
  try{await post('/chat/apply',{...body,request_id:request.current.id});onApplied()}
  catch(e){setError(e)}finally{setBusy(false)}
 }
 const axis=(key:'x'|'y'|'color',label:string)=><TextField select fullWidth disabled={busy} label={label} value={draft.card?.[key]??''} onChange={e=>patchCard({[key]:e.target.value||undefined})}><MenuItem value="">선택 안 함</MenuItem>{columns.map(c=><MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}</TextField>;
 const chart=preview?.card_result;
 return <Dialog open fullWidth maxWidth="lg" onClose={()=>!busy&&onClose()} aria-labelledby="assistant-review-title">
  <div className="dialog-heading"><div><h2 id="assistant-review-title">{proposalTitle(initial)}</h2></div><IconButton aria-label="제안 닫기" disabled={busy} onClick={onClose}><X/></IconButton></div>
  <DialogContent>
   <p>{initial.explanation}</p>
   {(preview?.knowledge??initial.knowledge)&&<ChatKnowledgePanel key={(preview?.knowledge??initial.knowledge)!.context_id} knowledge={(preview?.knowledge??initial.knowledge)!}/>}
   <div className="assistant-review-grid"><section className="assistant-review-controls">
   {draft.dataset&&<><TextField fullWidth label="데이터셋 이름" value={draft.dataset.name} onChange={e=>patchDataset({name:e.target.value})} disabled={busy} sx={{my:1}}/>
    {initial.dataset?.spec.pivot&&<Tabs value={editorMode} onChange={(_,mode)=>void switchEditor(mode)} aria-label="제안 쿼리 작성 방식" sx={{mb:2}}><Tab value="form" label="간편 작성기" disabled={busy||!pivot||!source}/><Tab value="sql" label="SQL 편집" disabled={busy||!!pivotError}/></Tabs>}
    {editorMode==='form'&&pivot&&source?<><PivotEditor source={source} pivot={pivot} disabled={busy} onChange={pivot=>patchDataset({spec:{...draft.dataset!.spec,pivot,raw_sql:null}})}/><SectionLabel>filter</SectionLabel><FilterBuilder columns={source.columns} filters={pivot.filters} disabled={busy} onChange={filters=>patchDataset({spec:{...draft.dataset!.spec,pivot:{...pivot,filters},raw_sql:null}})}/>{pivotError&&<p className="pivot-issue" role="status">{pivotError}</p>}</>:<TextField fullWidth multiline minRows={7} maxRows={18} label="조회 SQL" value={draft.dataset.spec.raw_sql??''} onChange={e=>patchDataset({spec:proposalSqlSpec(draft.dataset!.spec,e.target.value)})} disabled={busy} sx={{my:1,'& textarea':{fontFamily:'Consolas, monospace',fontSize:13}}}/>}
   </>}
   {draft.card&&<>
    <TextField fullWidth label="카드 제목" value={draft.card.title} onChange={e=>patchCard({title:e.target.value})} disabled={busy} sx={{my:1}}/>
    {draft.card.editor_mode==='python'?<><TextField fullWidth multiline minRows={12} maxRows={24} label="Plotly Python 스크립트" value={draft.card.python_script??''} onChange={e=>patchCard({python_script:e.target.value})} disabled={busy} sx={{my:1,'& textarea':{fontFamily:'Consolas, monospace',fontSize:12}}}/><p className="muted">df는 데이터셋 컬럼 그대로 · 집계·계산·정렬은 스크립트에서 처리 · 해당 카드 안에서만 실행</p></>:<>
    <div className="form-row"><TextField select fullWidth disabled={busy} label="차트" value={draft.card.kind} onChange={e=>patchCard({kind:e.target.value as CardSpec['kind']})}>{[['bar','막대'],['line','선'],['scatter','산점도'],['histogram','히스토그램'],['box','상자'],['wafer','Wafer map'],['kpi','KPI']].map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField><TextField select fullWidth disabled={busy} label="집계" value={draft.card.aggregate} onChange={e=>patchCard({aggregate:e.target.value as CardSpec['aggregate']})}>{[['none','없음'],['count','개수'],['sum','합계'],['avg','평균'],['min','최솟값'],['max','최댓값'],['yield','수율 (%)']].map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField></div>
    <div className="form-row">{axis('x','X축')}{axis('y','Y축 / 값')}{axis('color','색상 / 그룹')}</div></>}
    {draft.card.filters.length>0&&<p>필터: {draft.card.filters.map(f=>`${f.column} ${f.op} ${JSON.stringify(f.value)}`).join(' · ')}</p>}
   </>}
   <Button startIcon={<Play size={16}/>} onClick={runPreview} disabled={busy||!!pivotError} sx={{my:2}}>미리보기 실행</Button>
   {!valid&&<Alert severity="info" sx={{mb:2}}>변경한 내용으로 미리보기를 실행해 주세요.</Alert>}
   </section><section className="assistant-review-results">
   {busy&&<Loading label="검증 및 처리 중"/>}
   {valid&&draft.card&&chart&&<><h3>카드 미리보기</h3><p className="muted">{chartPeriodDescription(chart)}</p><div className="assistant-chart-preview">{!chart.rows.length&&!chart.figure?<p>조건에 맞는 데이터가 없습니다.</p>:draft.card.kind==='kpi'&&!chart.figure?<div className="kpi-value"><span>{chart.rows[0].value==null?'—':Number(chart.rows[0].value).toLocaleString('ko-KR',{maximumFractionDigits:2})}{draft.card.aggregate==='yield'?'%':''}</span></div>:<Plot card={draft.card} result={chart}/>}</div><p className="muted">{chart.meta.mode==='sample'?'표본':'조회 결과'} {chart.meta.returned_rows}행{chart.meta.truncated?' · 표시 한도로 일부 생략':''}</p></>}
   {valid&&preview?.query_result&&<><h3>조회 결과 · {preview.query_result.rows.length}행</h3><DataGrid columns={preview.query_result.columns} rows={preview.query_result.rows} maxHeight={170}/></>}
   </section></div>
   <ErrorNotice error={error}/><ErrorNotice error={board.error}/><ErrorNotice error={latest.error}/>
   {error instanceof ApiError&&error.status===409&&draft.card&&<Button onClick={async()=>{await board.refetch();setError(null)}}>최신 보드 확인</Button>}
   {draft.card&&board.data&&<p className="muted">대시보드 v{board.data.version}{draft.dataset?' · 새 데이터셋과 카드를 함께 저장합니다.':''}</p>}
  </DialogContent>
  <DialogActions sx={{px:3,py:2}}><Button onClick={onClose} disabled={busy}>닫기</Button><Button variant="contained" startIcon={<Save size={16}/>} disabled={busy||!valid||(!!draft.card&&!board.data)} onClick={save}>{draft.card?(draft.dataset?'데이터셋과 카드 적용':'카드 적용'):initial.kind==='query'?'데이터셋으로 저장':'데이터셋 생성'}</Button></DialogActions>
 </Dialog>;
}
