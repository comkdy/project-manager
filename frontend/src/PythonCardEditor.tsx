import {DatasetLabel} from './DatasetScopeBadge';
import {useEffect,useState} from 'react';
import {Button,Dialog,DialogActions,DialogContent,IconButton,MenuItem,Tab,Tabs,TextField} from '@mui/material';
import {Code2,Play,Save,X} from 'lucide-react';
import {useQuery} from '@tanstack/react-query';
import {api,post} from './api';
import type {CardSpec,ChartResult,Dataset,QueryContext} from './types';
import {Plot} from './Charts';
import {ErrorNotice,FilterBuilder,Loading} from './components';
import {CardPeriodControls} from './PeriodControls';
import {applyPythonConversion,canUseSimpleEditor,needsScriptExpansion,type PythonConversion} from './pythonCards';
import './python-cards.css';
import type {CardDraftEditorProps} from './cardDraftEditor';

export default function PythonCardEditor({initial,datasets,context,onClose,onSave,onSimple,draftNotice,onEdited,onDraftChange,onBusyChange}:{initial:CardSpec;datasets:Dataset[];context:QueryContext;onClose:()=>void;onSave:(c:CardSpec)=>Promise<void>;onSimple:(c:CardSpec)=>void}&CardDraftEditorProps){
 const [draft,setDraft]=useState(initial),[error,setError]=useState<unknown>(null),[busy,setBusy]=useState(false);
 useEffect(()=>{onDraftChange?.({card:draft,expressionDraft:''})},[draft,onDraftChange]);
 useEffect(()=>{onBusyChange?.(busy)},[busy,onBusyChange]);
 const [expanded,setExpanded]=useState(!needsScriptExpansion(initial));
 const conversion=useQuery({queryKey:['python-card-source',initial,context.project_id],queryFn:()=>post<PythonConversion>('/cards/python-script',{card:initial,context}),enabled:!!initial.dataset_id,retry:false});
 useEffect(()=>{if(!expanded&&conversion.data){setDraft(c=>applyPythonConversion(c,conversion.data!));setExpanded(true)}},[conversion.data,expanded]);
 const preparing=!expanded,canUseSimple=canUseSimpleEditor(draft,conversion.data?.simple_script,conversion.data?.card);
 const [preview,setPreview]=useState<{signature:string;result:ChartResult}|null>(null);
 const signature=JSON.stringify({draft,context}),valid=preview?.signature===signature;
 const patch=(value:Partial<CardSpec>)=>{if(busy||preparing)return;onEdited?.();setDraft(c=>({...c,...value}))};
 const dataset=useQuery({queryKey:['versions',context.project_id,draft.dataset_id,'latest'],queryFn:()=>api<Dataset>(`/datasets/${encodeURIComponent(draft.dataset_id)}/latest${context.project_id!=null?`?project_id=${context.project_id}`:''}`)});
 async function run(){setBusy(true);setError(null);try{setPreview({signature,result:await post<ChartResult>('/cards/query',{card:draft,context})})}catch(e){setError(e);setPreview(null)}finally{setBusy(false)}}
 async function save(){setBusy(true);setError(null);try{await onSave(draft);onClose()}catch(e){setError(e)}finally{setBusy(false)}}
 return <Dialog open fullWidth maxWidth="lg" onClose={busy?undefined:onClose} aria-labelledby="python-card-title">
  <div className="dialog-heading"><Code2 size={21}/><h2 id="python-card-title">카드 구성</h2><IconButton onClick={onClose} disabled={busy} aria-label="닫기"><X/></IconButton></div>
  <Tabs value="python" aria-label="카드 편집 방식" sx={{px:3}}><Tab value="simple" label="간편 편집기" disabled={!canUseSimple||busy||preparing} onClick={()=>onSimple({...draft,editor_mode:'simple'})}/><Tab value="python" label="Python 스크립트"/></Tabs>
  <DialogContent sx={{pt:1,pb:1}}>
   {draftNotice}<fieldset disabled={busy||preparing} style={{display:'contents'}}>
   <div className="python-card-fields"><TextField fullWidth size="small" label="카드 제목" value={draft.title} onChange={e=>patch({title:e.target.value})}/><TextField select fullWidth size="small" label="데이터셋" value={draft.dataset_id} disabled={busy||preparing} onChange={e=>{const d=datasets.find(d=>d.id===e.target.value);if(d)patch({dataset_id:d.id,dataset_version:d.version,filters:[],period:{use_dashboard:true}})}}>{datasets.map(d=><MenuItem key={d.id} value={d.id}><DatasetLabel dataset={d}/></MenuItem>)}</TextField></div>
   <div className="python-card-workspace"><section>
    <div className="python-card-input-summary"><strong>df</strong><span>데이터셋 컬럼 그대로 · 집계·계산·정렬은 아래 코드에서 수정</span></div>
    {preparing?(conversion.isError?<Button onClick={()=>void conversion.refetch()}>코드 변환 다시 시도</Button>:<Loading label="데이터셋 컬럼을 사용하는 코드로 준비하는 중"/>):<TextField fullWidth multiline size="small" minRows={11} maxRows={11} label="Plotly Python" value={draft.python_script??''} onChange={e=>patch({python_script:e.target.value})} slotProps={{htmlInput:{spellCheck:false,'aria-label':'Plotly Python 스크립트'}}} sx={{mt:1.5,'& textarea':{fontFamily:'Consolas, monospace',fontSize:12,lineHeight:1.6}}}/>}
    <div className="python-card-run"><Button variant="contained" startIcon={<Play size={15}/>} disabled={busy||preparing||!draft.python_script?.trim()} onClick={run}>실행</Button><span>선택한 카드 안에서만 실행</span></div>
    <details className="python-card-help"><summary>사용 가능한 데이터와 함수</summary><p>df는 선택한 데이터셋의 조회 결과입니다. 카드 필터·공통 기간은 조회 범위에 적용하며, 집계·계산·정렬은 스크립트에서 처리합니다.</p><p className="python-card-columns">{dataset.data?.columns.map(c=>c.name).join(' · ')}</p><p>df · px · go · pd · make_subplots는 이미 제공되므로 바로 사용하세요. import는 금지됩니다. 컬럼 계산, groupby, 정렬, 조건 선택, for를 지원하며 파일·네트워크·DB 접근은 지원하지 않습니다.</p><p>기간·필터에 해당하는 전체 데이터를 df로 사용합니다. 대용량 조회는 나눠 전달하고 차트 결과만 브라우저에 표시합니다. 격리 실행 60초 · 64개 trace · 표시 결과 4MB.</p>{!canUseSimple&&<p>사용자 정의 스크립트는 간편 편집기로 자동 변환할 수 없습니다.</p>}</details>
    <details className="python-card-help"><summary>필터</summary><CardPeriodControls card={draft} context={context} onChange={period=>patch({period})}/><FilterBuilder columns={dataset.data?.columns??[]} filters={draft.filters} onChange={filters=>patch({filters})}/></details>
   </section><section className="python-card-result" aria-label="스크립트 카드 미리보기">
    <h3>{draft.title}</h3>{busy?<Loading label="스크립트 실행 중"/>:valid&&preview?<><div className="python-card-preview"><Plot card={draft} result={preview.result}/></div><p>입력 데이터 {preview.result.meta.returned_rows.toLocaleString()}행</p></>:<div className="python-card-placeholder">실행하면 차트를 미리 볼 수 있습니다.</div>}
    <ErrorNotice error={error}/><ErrorNotice error={dataset.error}/>{preparing&&<ErrorNotice error={conversion.error}/>}
   </section></div>
  </fieldset></DialogContent><DialogActions sx={{px:3,pb:2}}><Button disabled={busy} onClick={onClose}>취소</Button><Button variant="contained" startIcon={<Save size={15}/>} disabled={busy||!valid} onClick={save}>카드 저장</Button></DialogActions>
 </Dialog>;
}
