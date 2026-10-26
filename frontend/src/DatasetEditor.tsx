import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Chip,Dialog,DialogContent,FormControlLabel,IconButton,Menu,MenuItem,Switch,Tab,Tabs,TextField,Tooltip,useMediaQuery} from '@mui/material';
import {Braces,Check,History,Pencil,Play,Save,X} from 'lucide-react';
import {useQueryClient} from '@tanstack/react-query';
import {api,post} from './api';
import PivotEditor from './PivotEditor';
import {initialPivot,pivotIssue} from './pivotSettings';
import type {Dataset,DatasetSpec,DatasetSql,PreviewResult,Proposal,QueryContext,SimpleEditorState,Source} from './types';
import {DataGrid,ErrorNotice,FilterBuilder,SectionLabel} from './components';
import OutputColumns from './OutputColumns';
import GroupColumns from './GroupColumns';
import {useGroupCompletion} from './useGroupCompletion';
import {useSimpleEditor} from './useSimpleEditor';
import {columnExpression,datasetSpec,editorSpec,type EditorSpec} from './datasetColumns';
import {editorRequest,sqlSnapshot,type SqlSnapshot} from './sqlDraft';
import DatasetImpactDialog from './DatasetImpactDialog';
import DatasetScopeBadge,{DatasetKindBadge} from './DatasetScopeBadge';
import {datasetImpactSignature,datasetImpactSnapshot,type DatasetImpactSnapshot} from './datasetImpactReview';
import {useLocalDraft,LocalDraftNotice,DraftConflictDialog} from './localDraft';
import {validDatasetDraft,datasetDraftComparison,type DatasetEditDraft} from './editorDraftValues';

const emptySpec=(source_id:string):DatasetSpec=>({source_id,columns:[],filters:[],joins:[],join_types:{},group_by:[],where_sql:null,having_sql:null,raw_sql:null});
type Props={dataset:Dataset;isNew?:boolean;versions?:Dataset[];versionsError?:unknown;onSelectVersion?:(version:number)=>void;editable?:boolean;catalog:Source[];context:QueryContext;projectName?:string;onClose:()=>void;onSaved:(saved:Dataset)=>Promise<void>;proposal?:Proposal['dataset']};

export default function DatasetEditor({dataset,isNew=false,versions=[dataset],versionsError,onSelectVersion,editable=true,catalog,context,projectName,onClose,onSaved,proposal}:Props){
 const client=useQueryClient();
 const initial=proposal?.spec??dataset.spec,projectId=context.project_id??null;
 const initialSqlMode=!isNew&&!initial.pivot&&(!!initial.raw_sql||!!initial.joins?.length||!!initial.having_sql);
 const canVersion=!isNew&&(dataset.project_id??null)===projectId;
 const compact=useMediaQuery('(max-width:480px)');
 const [spec,setSpec]=useState<EditorSpec>(()=>editorSpec(structuredClone(initial),catalog));
 const [sql,setSql]=useState(initial.raw_sql??'');
 const [name,setName]=useState(proposal?.name??dataset.name),[nameDraft,setNameDraft]=useState(proposal?.name??dataset.name);
 const [editingName,setEditingName]=useState(isNew&&!name.trim());
 const [saveAnchor,setSaveAnchor]=useState<HTMLElement|null>(null),[historyAnchor,setHistoryAnchor]=useState<HTMLElement|null>(null);
 const [editorMode,setEditorMode]=useState<'form'|'sql'>(initialSqlMode?'sql':'form');
 const [formSupport,setFormSupport]=useState<SimpleEditorState>({available:!initialSqlMode,reason:null});
 const [sourceTables,setSourceTables]=useState<string[]>([]);
 const [autoGroup,setAutoGroup]=useState(isNew||!initial.raw_sql&&!initial.group_by?.length);
 const [preview,setPreview]=useState<PreviewResult|null>(null),[busy,setBusy]=useState(true),[error,setError]=useState<unknown>(null),[compare,setCompare]=useState(1);
 const [pendingVersion,setPendingVersion]=useState<number|null>(null);
 const [impactReview,setImpactReview]=useState<DatasetImpactSnapshot|null>(null),[reviewNotice,setReviewNotice]=useState('');
 const revision=useRef(0),baseline=useRef('');
 const sqlOrigin=useRef<SqlSnapshot|null>(null);
 const normalBeforePivot=useRef<EditorSpec|null>(null);
 const latestVersion=Math.max(dataset.version,...versions.map(v=>v.version));
 const [acceptedVersion,setAcceptedVersion]=useState(latestVersion),[conflict,setConflict]=useState<Dataset|null>(null);
 const draftValue:DatasetEditDraft={name,nameDraft,editingName,spec,sql,editorMode,autoGroup,sqlOrigin:sqlOrigin.current};
 const localDraft=useLocalDraft({kind:'dataset',targetId:isNew?'new':dataset.id,projectId,baseVersion:isNew?null:acceptedVersion,value:draftValue,validate:validDatasetDraft,enabled:editable});
 const source=catalog.find(c=>c.id===spec.source_id);
 const inputs=source?.columns??[];
 const compatibility=useSimpleEditor(sql,spec.source_id,editable&&editorMode==='sql',spec.pivot);
 const simpleState=compatibility.value??formSupport;
 const columns=spec.columns.length?spec.columns:inputs.map(c=>({name:'',expression:columnExpression(c.name)}));
 const grouping=useGroupCompletion(columns.map(c=>c.expression),inputs.map(c=>c.name),null,editable&&editorMode==='form'&&autoGroup&&!spec.pivot);
 const groupBy=spec.pivot?[]:grouping.groups??spec.group_by??[];
 const pivotError=editorMode==='form'&&spec.pivot?pivotIssue(spec.pivot,source):null;
 const resolvedSpec={...spec,group_by:groupBy};
 const dirty=!!proposal||name!==dataset.name||JSON.stringify(editorMode==='sql'?sql:resolvedSpec)!==baseline.current;
 const filterColumns=inputs;
 const request=()=>editorRequest(editorMode,editorMode==='sql'?emptySpec(spec.source_id):datasetSpec(resolvedSpec),sql,sqlOrigin.current);
 const impactDraft={datasetId:dataset.id,viewedVersion:dataset.version,name,spec:preview?.spec??null,context,expectedVersion:acceptedVersion,editor:{mode:editorMode,spec:resolvedSpec,sql,origin:sqlOrigin.current}};
 const impactSignature=datasetImpactSignature(impactDraft),impactSignatureRef=useRef(impactSignature);
 impactSignatureRef.current=impactSignature;
 const contextSignature=JSON.stringify(context),contextSignatureRef=useRef(contextSignature),previousContext=useRef(contextSignature);
 contextSignatureRef.current=contextSignature;
 function acceptSql(draft:EditorSpec,text:string){sqlOrigin.current=sqlSnapshot(datasetSpec(draft),text);setSpec(draft);setSql(text)}

 useEffect(()=>{
  if(impactReview&&impactReview.signature!==impactSignature){setImpactReview(null);setReviewNotice('편집 내용이나 기준 버전이 변경되었습니다. 변경 영향을 다시 확인해 주세요.')}
 },[impactReview,impactSignature]);
 useEffect(()=>{
  if(previousContext.current===contextSignature)return;
  previousContext.current=contextSignature;revision.current++;setPreview(null);setImpactReview(null);setReviewNotice('조회 범위가 변경되었습니다. 조회 결과를 다시 미리보기해 주세요.');
 },[contextSignature]);

 useEffect(()=>{
  let active=true;const current=revision.current;
  post<DatasetSql>('/datasets/sql',{spec:initial,context:{project_id:projectId}}).then(result=>{
   if(!active||revision.current!==current)return;
   const draft=editorSpec(result.simple_editor.spec??result.spec,catalog);
   const mode=initialSqlMode||!result.simple_editor.available?'sql':'form';
   acceptSql(draft,result.sql);setSourceTables(result.source_tables);setFormSupport(result.simple_editor);setEditorMode(mode);
   baseline.current=JSON.stringify(mode==='sql'?result.sql:draft);
  }).catch(e=>{if(active)setError(e)}).finally(()=>{if(active)setBusy(false)});
  return()=>{active=false};
 },[]);
 function invalidate(){localDraft.markEdited();revision.current++;setPreview(null);setImpactReview(null);setReviewNotice('');setError(null)}
 function restoreDraft(value:DatasetEditDraft,version:number|null){
  revision.current++;setSpec(structuredClone(value.spec));setSql(value.sql);setName(value.name);setNameDraft(value.nameDraft);setEditingName(value.editingName);setEditorMode(value.editorMode);setAutoGroup(value.autoGroup);sqlOrigin.current=value.sqlOrigin;normalBeforePivot.current=null;setPreview(null);setImpactReview(null);setError(null);setBusy(false);if(version!==null)setAcceptedVersion(version);setReviewNotice('보관한 편집 내용을 불러왔습니다. 미리보기와 변경 영향을 다시 확인해 주세요.');
 }
 function update(patch:Partial<EditorSpec>){invalidate();setSpec({...spec,...patch})}
 function changeVersion(next:number){if(next===dataset.version)return;if(editable&&dirty)setPendingVersion(next);else onSelectVersion?.(next)}
 async function switchMode(next:'form'|'sql'){
  if(next===editorMode||grouping.pending||grouping.error||pivotError)return;
  if(next==='form'&&(compatibility.pending||!simpleState.available))return;
  const current=revision.current,wasDirty=dirty;setBusy(true);setError(null);
  try{
   const result=await post<DatasetSql>('/datasets/sql',{...request(),context:{project_id:projectId}});
   if(current!==revision.current)return;
   setFormSupport(result.simple_editor);setSourceTables(result.source_tables);
   if(next==='form'&&!result.simple_editor.available)return;
   const draft=editorSpec(result.simple_editor.spec??result.spec,catalog);
   localDraft.markEdited();acceptSql(draft,result.sql);setEditorMode(next);
   if(!wasDirty)baseline.current=JSON.stringify(next==='sql'?result.sql:draft);
  }catch(e){setError(e)}finally{setBusy(false)}
 }
 async function runPreview(){
  if(grouping.pending||grouping.error||pivotError)return;
  const current=++revision.current,previewContext=contextSignature;setBusy(true);setError(null);setPreview(null);setImpactReview(null);setReviewNotice('');
  try{
   const result=await post<PreviewResult>('/datasets/preview',{...request(),context:{...context,project_id:projectId,limit:30}});
   if(current===revision.current&&previewContext===contextSignatureRef.current){setPreview(result);acceptSql(editorSpec(result.simple_editor.spec??result.spec,catalog),result.canonical_sql);setFormSupport(result.simple_editor)}
  }catch(e){if(current===revision.current&&previewContext===contextSignatureRef.current)setError(e)}finally{setBusy(false)}
 }
 function applyName(){if(!nameDraft.trim()||busy)return;localDraft.markEdited();setName(nameDraft.trim());setEditingName(false)}
 async function save(mode:'new'|'version'){
  if(!editable||busy||editingName||!preview||!name.trim()||mode==='version'&&!canVersion)return;
  if(localDraft.blocked){setError(new Error('보관한 초안을 먼저 비교하거나 버린 뒤 저장해 주세요.'));return}
  setSaveAnchor(null);
  if(mode==='version'){if(latestVersion!==acceptedVersion){try{await refreshVersions()}catch(error){setError(error)}return}setError(null);setReviewNotice('');setImpactReview(datasetImpactSnapshot(impactDraft));return}
  setBusy(true);setError(null);
  try{
   const saved=await post<Dataset>('/datasets',{name:name.trim(),spec:preview.spec,project_id:projectId});
   localDraft.clearSaved();
   await onSaved(saved);onClose();
  }catch(e){setError(e)}finally{setBusy(false)}
 }
 async function saveReviewed(snapshot:DatasetImpactSnapshot){
  if(!editable||!canVersion||busy||editingName||!preview||localDraft.blocked||snapshot.signature!==impactSignatureRef.current)throw new Error('편집 내용이나 기준 버전이 변경되었습니다. 변경 영향을 다시 확인해 주세요.');
  setBusy(true);setError(null);
  try{
   const saved=await post<Dataset>(`/datasets/${encodeURIComponent(snapshot.datasetId)}/versions`,snapshot.body);
   localDraft.clearSaved();
   await onSaved(saved);onClose();
  }finally{setBusy(false)}
 }
 async function refreshVersions(){
  const list=await client.fetchQuery({queryKey:['versions',projectId,dataset.id],queryFn:({signal})=>api<Dataset[]>(`/datasets/${encodeURIComponent(dataset.id)}/versions${projectId!==null?`?project_id=${projectId}`:''}`,{signal}),staleTime:0});
  const latest=[...list].sort((a,b)=>b.version-a.version)[0];if(!latest)throw new Error('최신 저장 내용을 찾을 수 없습니다.');
  setImpactReview(null);setPreview(null);setConflict(latest);setReviewNotice('최신 저장 내용과 현재 편집 내용을 비교한 뒤 사용할 내용을 선택해 주세요.');
 }
 async function changeSource(id:string){
  const selected=catalog.find(s=>s.id===id);if(!selected)return;
  invalidate();normalBeforePivot.current=null;const current=revision.current;
  const initial={...emptySpec(id),raw_sql:selected.default_sql};
  setSpec(initial);setSql(selected.default_sql??'');setSourceTables([]);setAutoGroup(true);setBusy(true);
  try{
   const result=await post<DatasetSql>('/datasets/sql',{spec:initial,context:{project_id:projectId}});
   if(current!==revision.current)return;
   acceptSql(editorSpec(result.simple_editor.spec??result.spec,catalog),result.sql);setSourceTables(result.source_tables);setFormSupport(result.simple_editor);setEditorMode(result.simple_editor.available?'form':'sql');
  }catch(error){if(current===revision.current)setError(error)}finally{if(current===revision.current)setBusy(false)}
 }
 function changeTransform(pivotMode:boolean){
  if(!source||pivotMode===!!spec.pivot)return;
  if(pivotMode){normalBeforePivot.current=spec;update({...emptySpec(spec.source_id),pivot:initialPivot(source,spec)});setAutoGroup(false)}
  else{const pivot=spec.pivot!;update({...normalBeforePivot.current??emptySpec(spec.source_id),filters:pivot.filters,where_sql:pivot.where_sql,raw_sql:null,pivot:null});setAutoGroup(false)}
 }
 function changeGroups(group_by:string[]){setAutoGroup(false);update({group_by})}
 function toggleAutoGroup(checked:boolean){update({group_by:groupBy});setAutoGroup(checked)}
 const grouped=preview?.meta.before_mode==='source_sample';
 return <><Dialog open onClose={onClose} fullWidth maxWidth="lg" aria-labelledby="query-management-title" slotProps={{paper:{className:'dataset-editor-dialog'}}}>
  <div className="dialog-heading"><div className="dialog-icon"><Braces size={21}/></div><div><h2 id="query-management-title">{isNew?'데이터셋 추가':'쿼리 관리'}</h2></div><div className="query-heading-actions">
   {!isNew&&<Tooltip title="수정 이력"><span><IconButton id="query-history-button" aria-label="수정 이력" aria-haspopup="menu" aria-controls={historyAnchor?'query-history-menu':undefined} aria-expanded={!!historyAnchor} disabled={busy||editingName||!onSelectVersion} onClick={e=>setHistoryAnchor(e.currentTarget)}><History size={19}/></IconButton></span></Tooltip>}
   {editable&&<Tooltip title="저장"><span><IconButton id="query-save-button" className="query-save-button" aria-label="저장" aria-haspopup="menu" aria-controls={saveAnchor?'query-save-menu':undefined} aria-expanded={!!saveAnchor} disabled={busy||editingName} onClick={e=>setSaveAnchor(e.currentTarget)}><Save size={19}/></IconButton></span></Tooltip>}
   <IconButton onClick={onClose} aria-label="닫기"><X size={21}/></IconButton>
  </div></div>
  <Menu id="query-history-menu" anchorEl={historyAnchor} open={!!historyAnchor} onClose={()=>setHistoryAnchor(null)} anchorOrigin={{vertical:'bottom',horizontal:'right'}} transformOrigin={{vertical:'top',horizontal:'right'}} slotProps={{list:{'aria-labelledby':'query-history-button'},paper:{className:'query-action-menu'}}}>
   {[...versions].sort((a,b)=>b.version-a.version).map(d=><MenuItem key={d.version} selected={d.version===dataset.version} onClick={()=>{setHistoryAnchor(null);changeVersion(d.version)}}><div className="query-menu-label"><span>v{d.version}{d.version===latestVersion?' · 최신':''}{d.version===dataset.version?' · 현재 조회 중':''}</span><small>{d.name}</small></div></MenuItem>)}
  </Menu>
  <Menu id="query-save-menu" anchorEl={saveAnchor} open={!!saveAnchor} onClose={()=>setSaveAnchor(null)} anchorOrigin={{vertical:'bottom',horizontal:'right'}} transformOrigin={{vertical:'top',horizontal:'right'}} slotProps={{list:{'aria-labelledby':'query-save-button'},paper:{className:'query-action-menu'}}}>
   <MenuItem disabled={busy||!preview||!name.trim()||!canVersion} onClick={()=>save('version')}><div className="query-menu-label"><span>기존 쿼리 업데이트</span>{!canVersion&&<small>{isNew?'저장된 데이터셋에서 사용 가능':'공통 쿼리는 전체 프로젝트에서 수정 가능'}</small>}</div></MenuItem>
   <MenuItem disabled={busy||!preview||!name.trim()} onClick={()=>save('new')}>새 데이터셋으로 저장</MenuItem>
   {!preview&&<li className="query-menu-hint" role="presentation">미리보기 후 저장할 수 있습니다.</li>}
  </Menu>
  <DialogContent>
   {editable&&<LocalDraftNotice disabled={busy} draft={localDraft} onRestore={record=>restoreDraft(record.value,typeof record.baseVersion==='number'?record.baseVersion:null)}/>}
   <div className="query-dataset-heading"><div className="query-name-row">
    {editable&&editingName?<><TextField size="small" label="데이터셋 이름" value={nameDraft} autoFocus disabled={busy} onChange={e=>{localDraft.markEdited();setNameDraft(e.target.value)}} slotProps={{htmlInput:{maxLength:200}}} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();applyName()}if(e.key==='Escape'){e.stopPropagation();setEditingName(false)}}}/><IconButton aria-label="이름 적용" disabled={busy||!nameDraft.trim()} onClick={applyName}><Check size={18}/></IconButton><IconButton aria-label="이름 수정 취소" onClick={()=>setEditingName(false)}><X size={18}/></IconButton></>:<><strong>{name||'새 데이터셋'}</strong>{editable&&<Tooltip title="이름 수정"><IconButton aria-label="데이터셋 이름 수정" disabled={busy} onClick={()=>{setNameDraft(name);setEditingName(true)}}><Pencil size={16}/></IconButton></Tooltip>}</>}
   </div><DatasetKindBadge kind={isNew?'derived':dataset.kind}/><DatasetScopeBadge projectId={dataset.project_id} projectName={projectName}/><p className="query-source-subtitle">{sourceTables.join(' · ')||(source?`${source.schema_name}.${source.table_name}`:'원천 테이블 선택')}</p></div>
   <ErrorNotice error={versionsError}/><ErrorNotice error={error}/>
   {reviewNotice&&<Alert severity="info" sx={{mb:2}}>{reviewNotice}</Alert>}
   {pendingVersion!==null&&<Alert severity="warning" className="query-version-confirm" action={<><Button color="inherit" onClick={()=>setPendingVersion(null)}>계속 편집</Button><Button color="inherit" onClick={()=>{localDraft.discard();onSelectVersion?.(pendingVersion)}}>버리고 전환</Button></>}>편집 내용을 버리고 v{pendingVersion}으로 전환할까요?</Alert>}
   {editable?<>
    {isNew&&<TextField select fullWidth label="원천 테이블" value={spec.source_id} disabled={busy} onChange={e=>changeSource(e.target.value)} sx={{mb:2}}>{[...catalog].sort((a,b)=>a.schema_name.localeCompare(b.schema_name)||a.table_name.localeCompare(b.table_name)).map(s=><MenuItem key={s.id} value={s.id}>{s.schema_name}.{s.table_name}</MenuItem>)}</TextField>}
    <Tabs value={editorMode} onChange={(_,value)=>switchMode(value)} aria-label="쿼리 작성 방식" sx={{mb:2}}><Tab value="form" label="간편 작성기" disabled={busy||compatibility.pending||!simpleState.available}/><Tab value="sql" label="SQL 편집" disabled={busy||grouping.pending||!!grouping.error||!!pivotError}/></Tabs>
    {editorMode==='sql'?<>{(compatibility.pending||!simpleState.available)&&<p className="sql-editor-note" role="status">{compatibility.pending?'간편 작성기 전환 가능 여부를 확인 중입니다.':simpleState.reason}</p>}<TextField className="raw-sql-editor" label="조회 SQL" multiline minRows={compact?9:12} maxRows={26} fullWidth value={sql} disabled={busy} onChange={e=>{invalidate();setSql(e.target.value);setAutoGroup(false)}} spellCheck={false} slotProps={{htmlInput:{'aria-label':'조회 SQL',className:'sql-input'}}}/>{projectId!==null&&<p className="sql-editor-note">선택한 프로젝트의 데이터만 조회합니다. SQL 조건을 바꿔도 다른 프로젝트의 데이터는 포함되지 않습니다.</p>}</>:<>
     <div className="query-transform-switch" role="group" aria-label="데이터 변환"><Button className={!spec.pivot?'active':''} aria-pressed={!spec.pivot} disabled={busy} onClick={()=>changeTransform(false)}>기본 조회</Button><Button className={spec.pivot?'active':''} aria-pressed={!!spec.pivot} disabled={busy||!source} onClick={()=>changeTransform(true)}>피벗</Button></div>
     {spec.pivot&&source?<PivotEditor source={source} pivot={spec.pivot} disabled={busy} onChange={pivot=>update({pivot})}/>:<>
     <SectionLabel><span className="query-section-name">column</span> <Chip size="small" label={`${columns.length}개`}/></SectionLabel>
     <OutputColumns key={`columns-${spec.source_id}`} columns={columns} inputs={inputs} disabled={busy} onChange={columns=>update({columns})}/>
     <SectionLabel action={<FormControlLabel className="group-auto-control" control={<Switch size="small" checked={autoGroup} disabled={busy} onChange={(_,checked)=>toggleAutoGroup(checked)} slotProps={{input:{'aria-label':'group 자동 완성'}}}/>} label={grouping.pending?'자동 완성 중…':'자동'}/>}><span className="query-section-name">group</span> <Chip size="small" label={`${groupBy.length}개`}/></SectionLabel>
     <ErrorNotice error={grouping.error}/>
     <GroupColumns key={`groups-${spec.source_id}`} groups={groupBy} inputs={inputs} disabled={busy||grouping.pending} onChange={changeGroups}/>
     </>}
     <SectionLabel><span className="query-section-name">filter</span>{spec.pivot&&<span className="pivot-hint">피벗 전 원천 데이터에 적용</span>}</SectionLabel><FilterBuilder columns={filterColumns} filters={spec.pivot?.filters??spec.filters} disabled={busy} onChange={filters=>update(spec.pivot?{pivot:{...spec.pivot,filters}}:{filters})}/>
     {pivotError&&<p className="pivot-issue" role="status">{pivotError}</p>}
     {spec.where_sql&&<div className="query-sql-condition"><span>SQL에 설정된 행 조건</span><code>{spec.where_sql}</code><Button size="small" disabled={busy} onClick={()=>switchMode('sql')}>SQL에서 수정</Button></div>}
    </>}
    <div className="preview-heading"><SectionLabel>{editorMode==='sql'?'조회 결과':'데이터 미리보기'}</SectionLabel><Button startIcon={<Play size={15}/>} onClick={runPreview} disabled={busy||grouping.pending||!!grouping.error||!!pivotError||editorMode==='sql'&&!sql.trim()}>{busy?'확인 중…':editorMode==='sql'?'SQL 실행':'미리보기 실행'}</Button></div>
    {preview?<>{preview.meta.before_mode!=='unavailable'&&<Tabs value={compare} onChange={(_,v)=>setCompare(v)}><Tab label={grouped?'집계 전 표본':'변경 전'}/><Tab label={grouped?'집계 결과':'변경 후'}/></Tabs>}<DataGrid columns={compare===1||preview.meta.before_mode==='unavailable'?preview.columns:(preview.before_rows?.length?Object.keys(preview.before_rows[0]).map(name=>({name,type:''})):inputs)} rows={compare===1||preview.meta.before_mode==='unavailable'?preview.rows:preview.before_rows??[]} maxHeight={260}/><Alert severity="success" sx={{mt:1}}>검증 완료 · {preview.rows.length}행. 상단 저장 버튼에서 저장할 수 있습니다.</Alert>{grouped&&<Alert severity="info" sx={{mt:1}}>집계 전 표본은 WHERE를 적용한 입력 행입니다. 집계 결과와 일대일 대응하지 않으며 HAVING 적용 전 표본입니다.</Alert>}</>:<div className="preview-empty">미리보기 후 저장할 수 있습니다. 저장하면 연결된 카드에도 최신 정의가 적용됩니다.</div>}
   </>:<><p className="query-readonly-note">버전을 선택하면 해당 버전의 SQL과 원천 테이블을 확인할 수 있습니다.</p><pre className="sql-preview">{sql||'쿼리를 불러오는 중…'}</pre></>}
  </DialogContent>
 </Dialog>{impactReview?.signature===impactSignature&&<DatasetImpactDialog key={impactReview.signature} snapshot={impactReview} projectName={projectName} onClose={()=>setImpactReview(null)} onSave={saveReviewed} onRefreshVersions={refreshVersions}/>}
 <DraftConflictDialog open={!!conflict} title="쿼리 변경 내용 비교" mine={datasetDraftComparison(draftValue)} latest={conflict?datasetDraftComparison({name:conflict.name,spec:conflict.spec,sql:conflict.spec.raw_sql??'',editorMode:conflict.spec.raw_sql?'sql':'form'}):null} mineLabel={`내 편집 내용 · 기준 v${acceptedVersion}`} latestLabel={`최신 저장 내용 · v${conflict?.version??''}`} onClose={()=>setConflict(null)} onUseLatest={()=>{if(!conflict)return;localDraft.discard();restoreDraft({name:conflict.name,nameDraft:conflict.name,editingName:false,spec:editorSpec(conflict.spec,catalog),sql:conflict.spec.raw_sql??'',editorMode:conflict.spec.raw_sql?'sql':'form',autoGroup:false,sqlOrigin:null},conflict.version);setConflict(null)}} onKeepMine={()=>{if(!conflict)return;localDraft.markEdited();setAcceptedVersion(conflict.version);revision.current++;setPreview(null);setImpactReview(null);setConflict(null);setReviewNotice('내 내용을 유지했습니다. 최신 저장 기준으로 미리보기와 변경 영향을 다시 확인해 주세요.')}}/>
 </>
}
