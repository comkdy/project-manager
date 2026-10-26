import {useState} from 'react';
import {Alert,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle,IconButton,MenuItem,TextField} from '@mui/material';
import {Check,History,Plus,Save,ShieldCheck,Trash2,X} from 'lucide-react';
import {api,ApiError,post} from './api';
import type {Project,Source} from './types';
import {ErrorNotice} from './components';
import {blankBinding,blankContent,detailFields,kindNames,statusNames} from './semanticTypes';
import type {BindingContent,KnowledgeContent,KnowledgeItem,KnowledgeKind,KnowledgeList,Predicate,ReviewInfo} from './semanticTypes';
import {DraftConflictDialog,LocalDraftNotice,useLocalDraft} from './localDraft';
import {definitionComparison,definitionInput,definitionVersion,mappingCanRebase,mappingVersion,validDefinitionInput,validMappingInput} from './semanticDrafts';
import type {DefinitionInput,MappingInput,PredicateDraft,ValueKind} from './semanticDrafts';
import {evaluationDatasetNotice,evaluationScopeLabel} from './evaluationKnowledge';
import EvaluationKnowledgeSummary from './EvaluationKnowledgeSummary';

function LinesField({label,value,onChange,disabled=false}:{label:string;value:string;onChange:(value:string)=>void;disabled?:boolean}){
 return <TextField label={label} value={value} disabled={disabled} multiline minRows={2} fullWidth
  helperText="한 줄에 하나씩 입력하세요." onChange={e=>onChange(e.target.value)}/>;
}

function draftPredicate(p:Predicate):PredicateDraft{
 const value=p.value;
 return {source_id:p.source_id,column:p.column,op:p.op,value_kind:Array.isArray(value)?(typeof value[0]==='number'?'number_list':'text_list'):typeof value==='number'?'number':typeof value==='boolean'?'boolean':'text',
  value_text:Array.isArray(value)?value.join('\n'):value==null?'':String(value)};
}
function finishPredicate(p:PredicateDraft):Predicate{
 let value:Predicate['value']=p.value_text;
 if(p.op==='is_null'||p.op==='is_not_null')value=null;
 else if(p.value_kind==='number'){
  if(!p.value_text.trim()||!Number.isFinite(Number(p.value_text)))throw new Error('조건값에 숫자를 입력하세요.');
  value=Number(p.value_text);
 }else if(p.value_kind==='boolean')value=p.value_text==='true';
 else if(p.value_kind.endsWith('_list')){
  const parts=p.value_text.split('\n').filter(v=>v.trim());
  if(!parts.length)throw new Error('목록 조건값을 한 줄에 하나씩 입력하세요.');
  if(p.value_kind==='number_list'&&parts.some(v=>!Number.isFinite(Number(v))))throw new Error('목록의 모든 값을 숫자로 입력하세요.');
  value=p.value_kind==='number_list'?parts.map(Number):parts;
 }
 return {source_id:p.source_id,column:p.column,op:p.op,value};
}

export default function SemanticEditor({item:initialItem,initialKind,items,sources,projects=[],admin,onSaved,onClose,onEvaluationQuestion,active=true}:{
 item:KnowledgeItem|null;initialKind:KnowledgeKind;items:KnowledgeItem[];sources:Source[];projects?:Project[];admin:boolean;active?:boolean;
 onSaved:(item:KnowledgeItem)=>Promise<void>;onClose:()=>void;onEvaluationQuestion?:(questionId:string)=>void;
}){
 const [item,setItem]=useState(initialItem),[remoteItem,setRemoteItem]=useState<KnowledgeItem|null>(null);
 const [kind,setKind]=useState<KnowledgeKind>(item?.kind??initialKind);
 const [scope,setScope]=useState<'common'|'database'>(item?.scope??(['data','relation','query','evaluation'].includes(initialKind)?'database':'common'));
 const [content,setContent]=useState<KnowledgeContent>(item?.content??blankContent());
 const [binding,setBinding]=useState<BindingContent>(item?.binding?.content??blankBinding());
 const [predicates,setPredicates]=useState<PredicateDraft[]>((item?.binding?.content.predicates??[]).map(draftPredicate));
 const [aliasesText,setAliasesText]=useState(content.aliases.join('\n')),[unresolvedText,setUnresolvedText]=useState(content.unresolved.join('\n'));
 const [definitionBase,setDefinitionBase]=useState(definitionVersion(item)),[mappingBase,setMappingBase]=useState(mappingVersion(item));
 const [conflict,setConflict]=useState<'definition'|'binding'|null>(null);
 const [dirty,setDirty]=useState(false),[bindingDirty,setBindingDirty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 const [checkResult,setCheckResult]=useState(item?.validation);
 const [history,setHistory]=useState<KnowledgeItem[]|null>(null),[historical,setHistorical]=useState<KnowledgeItem|null>(null);
 const [bindingHistory,setBindingHistory]=useState<(ReviewInfo&{version:number;content:BindingContent})[]|null>(null);
 const [review,setReview]=useState<{target:'definition'|'binding';decision:'approve'|'retire'}|null>(null);
 const latestItem=[item,remoteItem,items.find(v=>v.id===item?.id)].filter((v):v is KnowledgeItem=>!!v).sort((a,b)=>b.revision-a.revision||(b.binding?.revision??0)-(a.binding?.revision??0))[0]??null;
 const definitionValue:DefinitionInput={kind,scope,content,aliasesText,unresolvedText};
 const mappingValue:MappingInput={binding:{sources:binding.sources,expression:binding.expression,notes:binding.notes},predicates};
 const definitionDraft=useLocalDraft({kind:'knowledge-definition',targetId:item?.id??`new:${initialKind}`,projectId:null,baseVersion:definitionBase,value:definitionValue,validate:(v)=>validDefinitionInput(v)&&(!item||v.kind===item.kind&&v.scope===item.scope),enabled:admin&&kind!=='evaluation'});
 const mappingDraft=useLocalDraft({kind:'knowledge-mapping',targetId:item?.id??'new',projectId:null,baseVersion:mappingBase,value:mappingValue,validate:validMappingInput,enabled:admin&&!!item&&kind!=='evaluation'});
 const definitionChanged=!!item&&definitionBase!==definitionVersion(latestItem),mappingChanged=!!item&&mappingBase!==mappingVersion(latestItem);
 const definitionBlocked=busy||definitionChanged||definitionDraft.blocked,mappingBlocked=busy||definitionChanged||mappingChanged||mappingDraft.blocked||definitionDraft.blocked;
 const readonly=!admin||!!historical||busy;
 const shown=historical?.content??content;
 const evaluationContext=(historical??item)?.evaluation_context,evaluationNotice=evaluationDatasetNotice(evaluationContext);
 const issues=checkResult?.definition_issues??[],mappingIssues=checkResult?.binding_issues??[];
 function change(patch:Partial<KnowledgeContent>){definitionDraft.markEdited();setContent(v=>({...v,...patch}));setDirty(true);setCheckResult(undefined)}
 function detail(key:string,value:string|null){change({details:{...content.details,[key]:value}})}
 function mapChange(patch:Partial<BindingContent>){mappingDraft.markEdited();setBinding(v=>({...v,...patch}));setBindingDirty(true)}
 function applyDefinition(value:DefinitionInput,base:string,edited:boolean){setKind(value.kind);setScope(value.scope);setContent(value.content);setAliasesText(value.aliasesText);setUnresolvedText(value.unresolvedText);setDefinitionBase(base);setDirty(edited);setCheckResult(undefined)}
 function applyMapping(value:MappingInput,base:string,edited:boolean){setBinding({...value.binding,predicates:[]});setPredicates(value.predicates);setMappingBase(base);setBindingDirty(edited)}
 const latestDefinition=latestItem?definitionInput(latestItem.kind,latestItem.scope,latestItem.content):definitionValue;
 const latestMapping:MappingInput={binding:{sources:latestItem?.binding?.content.sources??[],expression:latestItem?.binding?.content.expression??'',notes:latestItem?.binding?.content.notes??''},predicates:(latestItem?.binding?.content.predicates??[]).map(draftPredicate)};
 function resolveConflict(keep:boolean){
  if(!latestItem)return;
  setItem(latestItem);
  if(conflict==='definition'){applyDefinition(keep?definitionValue:latestDefinition,definitionVersion(latestItem),keep);if(keep)definitionDraft.markEdited();else definitionDraft.discard()}
  else{applyMapping(keep?mappingValue:latestMapping,mappingVersion(latestItem),keep);if(keep)mappingDraft.markEdited();else mappingDraft.discard()}
  setConflict(null);setError(null);
 }
 async function run(action:()=>Promise<unknown>,target:'definition'|'binding'='definition'){
  setBusy(true);setError(null);
  try{await action()}catch(e){
   setError(e);
   if(item&&e instanceof ApiError&&e.status===409){
    try{const latest=(await api<KnowledgeList>('/semantic/items')).items.find(v=>v.id===item.id);if(latest){setRemoteItem(latest);setConflict(target)}}catch{/* Keep the local input and original error if refresh fails. */}
   }
  }finally{setBusy(false)}
 }
 async function saveDefinition(){
  if(definitionBlocked)return;
  await run(async()=>{
   const result=await post<KnowledgeItem>(item?`/semantic/items/${item.id}/versions`:'/semantic/items',
    item?{expected_revision:item.revision,content}:{kind,scope,content});
   definitionDraft.clearSaved();await onSaved(result);
  });
 }
 async function saveMapping(){
  if(!item||mappingBlocked)return;
  await run(async()=>{const result=await post<KnowledgeItem>(`/semantic/items/${item.id}/bindings`,{
   definition_version:item.version,expected_revision:item.binding?.revision??0,
   content:{...binding,predicates:predicates.map(finishPredicate)}});
   mappingDraft.clearSaved();await onSaved(result);
  },'binding');
 }
 async function submitReview(note:string,onSuccess:()=>void){
  if(!item||!review||definitionBlocked||review.target==='binding'&&mappingBlocked)return;
  await run(async()=>{
   const result=await post<KnowledgeItem>(`/semantic/items/${item.id}/${review.target==='binding'?'bindings/review':'review'}`,{
    expected_revision:review.target==='binding'?item.binding!.revision:item.revision,
    version:review.target==='binding'?item.binding!.version:item.version,
    ...(review.target==='binding'?{definition_version:item.version}:{}),decision:review.decision,note});
   onSuccess();setReview(null);await onSaved(result);
  },review.target);
 }
 function updatePredicate(index:number,patch:Partial<PredicateDraft>){
  mappingDraft.markEdited();setPredicates(values=>values.map((v,i)=>i===index?{...v,...patch}:v));setBindingDirty(true);
 }
 return <section className="knowledge-editor" aria-label="지식 상세">
  <div className="knowledge-detail-heading"><div><span className="knowledge-eyebrow">{item?'KNOWLEDGE DETAIL':'NEW KNOWLEDGE'}</span>
   <h3>{item?item.content.name:'새 업무 지식'}</h3></div>
   <IconButton aria-label="지식 상세 닫기" onClick={onClose}><X size={19}/></IconButton></div>
  {item&&<div className="knowledge-badges"><Chip size="small" label={`${kindNames[item.kind]} · v${historical?.version??item.version}`}/>
   <Chip size="small" color={(historical??item).status==='approved'?'success':'default'} label={statusNames[(historical??item).status]}/>
   <span>{kind!=='evaluation'&&(item.scope==='common'?'공통 업무 정의':'현재 DB 전용')}{item.published_version?` · 제공 중 v${item.published_version}`:' · 승인된 버전 없음'}</span></div>}
  <ErrorNotice error={error}/>
  {evaluationContext&&<div className="knowledge-help"><p>적용 범위: {evaluationScopeLabel(evaluationContext.dataset??evaluationContext.case_context??{project_id:null},projects).replace('공통 데이터셋','공통')}</p>{evaluationContext.dataset&&<p>등록한 데이터셋: {evaluationContext.dataset.name} · v{evaluationContext.dataset.version}</p>}{onEvaluationQuestion&&<Button size="small" variant="outlined" onClick={()=>onEvaluationQuestion(evaluationContext.question_id)}>원문·확인 요청 보기</Button>}</div>}
  {evaluationNotice&&<Alert severity="warning" sx={{mb:2}}>{evaluationNotice}</Alert>}
  {admin&&!historical&&kind!=='evaluation'&&<><LocalDraftNotice disabled={busy} active={active} draft={definitionDraft} onRestore={record=>applyDefinition(record.value,String(record.baseVersion),true)}/>
   {definitionChanged&&<Alert severity="warning" action={<Button onClick={()=>setConflict('definition')}>내 내용과 최신 정의 비교</Button>}>정의 버전 또는 검토 상태가 바뀌었습니다. 내용을 확인하기 전에는 저장·승인할 수 없습니다.</Alert>}</>}
  {item&&admin&&<div className="knowledge-actions">
   <Button size="small" startIcon={<History size={15}/>} disabled={busy} onClick={()=>run(async()=>setHistory(await api<KnowledgeItem[]>(`/semantic/items/${item.id}/versions`)))}>버전 이력</Button>
   {kind!=='evaluation'&&<Button size="small" startIcon={<ShieldCheck size={15}/>} disabled={mappingBlocked||dirty||bindingDirty} onClick={()=>run(async()=>{
    const value=await post<KnowledgeItem>(`/semantic/items/${item.id}/validate`,{version:item.version,live_schema:true});setCheckResult(value.validation);
   })}>현재 스키마로 검증</Button>}
  </div>}
  {historical&&<Alert severity="info" action={<Button onClick={()=>setHistorical(null)}>현재 초안으로</Button>}>v{historical.version}의 보존된 정의입니다.</Alert>}
  <div className="knowledge-form">
   {!item&&kind!=='evaluation'&&<div className="knowledge-fields">
    <TextField select disabled={readonly} label="지식 종류" value={kind} onChange={e=>{const next=e.target.value as KnowledgeKind;definitionDraft.markEdited();setDirty(true);setKind(next);setContent(v=>({...v,details:{}}));if(['data','relation','query'].includes(next))setScope('database')}}>
     {Object.entries(kindNames).filter(([value])=>value!=='evaluation').map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField>
    <TextField select label="정의 범위" value={scope} disabled={readonly||['data','relation','query'].includes(kind)} onChange={e=>{definitionDraft.markEdited();setDirty(true);setScope(e.target.value as typeof scope)}}>
     <MenuItem value="common">공통 업무 정의</MenuItem><MenuItem value="database">현재 DB 전용</MenuItem></TextField>
   </div>}
   {kind!=='evaluation'&&<><TextField required label="이름" value={shown.name} disabled={readonly} onChange={e=>change({name:e.target.value})} inputProps={{maxLength:200}} fullWidth/>
   <TextField label="업무 영역" placeholder="담당 업무·공정 등 실제 구분을 입력하세요" value={shown.domain} disabled={readonly} onChange={e=>change({domain:e.target.value})} fullWidth/>
   <TextField label="업무 설명" value={shown.description} disabled={readonly} onChange={e=>change({description:e.target.value})} multiline minRows={3} fullWidth/>
   <LinesField key={`aliases-${historical?.version??'edit'}`} label="동의어·약어·한영 표현" value={historical?shown.aliases.join('\n'):aliasesText} disabled={readonly} onChange={text=>{setAliasesText(text);change({aliases:text.split('\n').map(v=>v.trim()).filter(Boolean)})}}/></>}
   {kind==='metric'&&<TextField select label="지표 유형" value={shown.details.metric_type??''} disabled={readonly} onChange={e=>detail('metric_type',e.target.value||null)}>
    <MenuItem value="">미정</MenuItem><MenuItem value="ratio">비율</MenuItem><MenuItem value="count">개수</MenuItem><MenuItem value="sum">합계</MenuItem><MenuItem value="other">기타</MenuItem></TextField>}
   {kind==='relation'&&<TextField select label="카디널리티" value={shown.details.cardinality??''} disabled={readonly} onChange={e=>detail('cardinality',e.target.value||null)}>
    <MenuItem value="">미정</MenuItem>{[['one_to_one','일대일'],['many_to_one','다대일'],['one_to_many','일대다'],['many_to_many','다대다']].map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>}
   {kind==='evaluation'&&<EvaluationKnowledgeSummary details={shown.details} terms={evaluationContext?.term_links} execution={evaluationContext?.execution_evidence}/>}
   {kind==='evaluation'?<><p className="knowledge-help">용어의 뜻과 동의어는 기존 AI 지식을 참조합니다. 추가 설명이 필요하면 확인 요청에서 자연어로 답변합니다.</p><details><summary>보존된 평가 내용·조건</summary>{shown.description&&<p style={{whiteSpace:'pre-wrap'}}>{shown.description}</p>}{detailFields.evaluation.filter(([field])=>!!shown.details[field]).map(([field,label])=><div key={field}><strong>{label}</strong><p style={{whiteSpace:'pre-wrap'}}>{String(shown.details[field])}</p></div>)}</details></>:
   <div className={kind==='metric'?'knowledge-fields':''}>
    {detailFields[kind].filter(([field])=>field!=='analysis_result'||!!shown.details[field]).map(([field,label])=><TextField key={field} label={label} placeholder="미정 — 업무 담당자가 지정" disabled={readonly||field==='analysis_result'}
     value={shown.details[field]??''} onChange={e=>detail(field,e.target.value)} multiline minRows={field==='sql'?7:2} fullWidth
     className={field==='sql'?'knowledge-sql':undefined} sx={{mb:kind==='metric'?0:2}}/>)}
   </div>}
   {kind!=='evaluation'&&<><LinesField key={`unresolved-${historical?.version??'edit'}`} label="아직 결정하지 않은 사항" value={historical?shown.unresolved.join('\n'):unresolvedText} disabled={readonly} onChange={text=>{setUnresolvedText(text);change({unresolved:text.split('\n').map(v=>v.trim()).filter(Boolean)})}}/>
   <TextField select label="참조하는 승인된 정의" disabled={readonly} value={shown.references.map(r=>`${r.item_id}:${r.version}`)}
    slotProps={{select:{multiple:true}}} onChange={e=>{const values=typeof e.target.value==='string'?e.target.value.split(','):e.target.value as string[];
     change({references:values.map(v=>{const [item_id,version]=v.split(':');return {item_id,version:Number(version)}})})}}>
    {shown.references.filter(r=>!items.some(i=>i.id===r.item_id&&i.published_version===r.version)).map(r=><MenuItem key={`${r.item_id}:${r.version}`} value={`${r.item_id}:${r.version}`}>이전 참조 · v{r.version} ({r.item_id.slice(0,8)})</MenuItem>)}
    {items.filter(v=>v.id!==item?.id&&v.kind!=='evaluation'&&v.published_version&&(scope==='database'||v.scope==='common')).map(v=><MenuItem key={v.id} value={`${v.id}:${v.published_version}`}>{v.content.name} · v{v.published_version}</MenuItem>)}
   </TextField>
   <TextField label="변경 이유" value={shown.change_note} disabled={readonly} multiline onChange={e=>change({change_note:e.target.value})}/></>}
  </div>
  {issues.length>0&&!historical&&<Alert severity="warning" className="knowledge-validation"><strong>{kind==='evaluation'?'확인 요청에서 보완할 내용':'정의 승인 전 확인'}</strong><ul>{issues.map((v,i)=><li key={i}>{v}</li>)}</ul></Alert>}
  {!historical&&admin&&<div className="knowledge-actions">
   {kind!=='evaluation'&&<Button variant="contained" startIcon={<Save size={16}/>} disabled={definitionBlocked||!content.name.trim()||(!!item&&!dirty)} onClick={saveDefinition}>{item?'새 초안 버전 저장':'초안 저장'}</Button>}
   {kind!=='evaluation'&&item?.status==='draft'&&<Button startIcon={<Check size={16}/>} disabled={definitionBlocked||dirty||issues.length>0} onClick={()=>{setReview({target:'definition',decision:'approve'})}}>정의 승인</Button>}
   {item&&item.status!=='retired'&&<Button color="inherit" disabled={definitionBlocked||dirty} onClick={()=>{setReview({target:'definition',decision:'retire'})}}>이 버전 폐기</Button>}
  </div>}
  {item&&!historical&&kind!=='evaluation'&&<>
   <div className="knowledge-section-heading"><h4>현재 DB의 물리 매핑</h4><span>{item.binding?`매핑 v${item.binding.version} · ${statusNames[item.binding.status]}`:'매핑 미등록'}</span></div>
   {admin&&<LocalDraftNotice disabled={busy} active={active} draft={mappingDraft} onRestore={record=>applyMapping(record.value,String(record.baseVersion),true)}/>}
   {mappingChanged&&admin&&<Alert severity="warning" action={<Button onClick={()=>setConflict('binding')}>내 내용과 최신 매핑 비교</Button>}>정의 또는 매핑의 버전·검토 상태가 바뀌었습니다. 저장 전에 다시 확인하세요.</Alert>}
   <p className="knowledge-help">공통 정의를 이 DB의 테이블·컬럼·코드 조건에 연결합니다. 매핑은 DB별로 보존됩니다.</p>
   {item.binding?.review_note&&<p className="knowledge-help">매핑 검토 기록: {item.binding.review_note}</p>}
   {admin&&item.binding&&<Button size="small" startIcon={<History size={14}/>} onClick={()=>run(async()=>setBindingHistory(await api(`/semantic/items/${item.id}/bindings?definition_version=${item.version}`)))}>매핑 버전 이력</Button>}
   {dirty&&<Alert severity="info">정의 변경을 먼저 저장하세요. 새 정의 버전의 매핑은 다시 검토해야 합니다.</Alert>}
   {kind==='relation'&&<Alert severity="info">연결 컬럼을 대응 순서대로 지정하세요. 관계는 AI 참고 정보로 사용되며 SQL을 자동으로 변경하지 않습니다.</Alert>}
   <fieldset disabled={!admin||dirty||busy} className="knowledge-mapping">
    {binding.sources.map((ref,index)=><div className="knowledge-source-row" key={index}>
     <TextField select label="원천 테이블" value={ref.source_id} onChange={e=>mapChange({sources:binding.sources.map((r,i)=>i===index?{...r,source_id:e.target.value,columns:[]}:r)})}>
      {!sources.some(s=>s.id===ref.source_id)&&ref.source_id&&<MenuItem value={ref.source_id}>사용할 수 없는 테이블</MenuItem>}
      {sources.map(s=><MenuItem key={s.id} value={s.id}>{s.schema_name}.{s.table_name}</MenuItem>)}</TextField>
     <TextField select={kind==='relation'} label={kind==='relation'?'연결 방향':'역할'} value={ref.role}
      onChange={e=>mapChange({sources:binding.sources.map((r,i)=>i===index?{...r,role:e.target.value}:r)})}>
      {kind==='relation'&&[<MenuItem key="empty" value="">미정</MenuItem>,<MenuItem key="left" value="left">왼쪽 (left)</MenuItem>,<MenuItem key="right" value="right">오른쪽 (right)</MenuItem>]}</TextField>
     <TextField select label="컬럼 (선택 순서 유지)" value={ref.columns} slotProps={{select:{multiple:true}}}
      onChange={e=>mapChange({sources:binding.sources.map((r,i)=>i===index?{...r,columns:typeof e.target.value==='string'?e.target.value.split(','):e.target.value as string[]}:r)})}>
      {ref.columns.filter(c=>!sources.find(s=>s.id===ref.source_id)?.columns.some(x=>x.name===c)).map(c=><MenuItem key={c} value={c}>{c} (없음)</MenuItem>)}
      {sources.find(s=>s.id===ref.source_id)?.columns.map(c=><MenuItem key={c.name} value={c.name}>{c.name} · {c.type}</MenuItem>)}</TextField>
     {admin&&<IconButton aria-label="테이블 매핑 제거" onClick={()=>mapChange({sources:binding.sources.filter((_,i)=>i!==index)})}><Trash2 size={16}/></IconButton>}
    </div>)}
    {admin&&<Button startIcon={<Plus size={15}/>} onClick={()=>mapChange({sources:[...binding.sources,{source_id:'',role:'',columns:[]}]})}>테이블 매핑 추가</Button>}
    <h5>코드·조건 매핑</h5><p className="knowledge-help">아래 조건을 모두 적용하는 의미입니다. 조건이 없으면 추가하지 마세요.</p>
    {predicates.map((p,index)=><div className="knowledge-predicate" key={index}>
     <div className="knowledge-fields">
      <TextField select label="조건 테이블" value={p.source_id} onChange={e=>updatePredicate(index,{source_id:e.target.value,column:''})}>
       {sources.filter(s=>binding.sources.some(r=>r.source_id===s.id)).map(s=><MenuItem key={s.id} value={s.id}>{s.schema_name}.{s.table_name}</MenuItem>)}</TextField>
      <TextField select label="조건 컬럼" value={p.column} onChange={e=>updatePredicate(index,{column:e.target.value})}>
       {sources.find(s=>s.id===p.source_id)?.columns.map(c=><MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}</TextField>
      <TextField select label="비교" value={p.op} onChange={e=>{const op=e.target.value as Predicate['op'];updatePredicate(index,{op,value_kind:op==='in'?'text_list':'text',value_text:''})}}>
       {Object.entries({eq:'같음',ne:'다름',gt:'초과',gte:'이상',lt:'미만',lte:'이하',in:'목록 중 하나',is_null:'값 없음',is_not_null:'값 있음'}).map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>
      {!p.op.startsWith('is_')&&<TextField select label="값 유형" value={p.value_kind} onChange={e=>updatePredicate(index,{value_kind:e.target.value as ValueKind,value_text:e.target.value==='boolean'?'true':''})}>
       {(p.op==='in'?[['text_list','문자열 목록'],['number_list','숫자 목록']]:[['text','문자열'],['number','숫자'],['boolean','참/거짓']]).map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>}
     </div>
     {!p.op.startsWith('is_')&&<TextField fullWidth select={p.value_kind==='boolean'} label={p.value_kind.endsWith('_list')?'조건값 (한 줄에 하나)':'조건값'} value={p.value_text} multiline={p.value_kind.endsWith('_list')} onChange={e=>updatePredicate(index,{value_text:e.target.value})}>
      {p.value_kind==='boolean'&&[<MenuItem key="true" value="true">참</MenuItem>,<MenuItem key="false" value="false">거짓</MenuItem>]}</TextField>}
     {admin&&<Button size="small" color="inherit" startIcon={<Trash2 size={14}/>} onClick={()=>{mappingDraft.markEdited();setPredicates(v=>v.filter((_,i)=>i!==index));setBindingDirty(true)}}>조건 제거</Button>}
    </div>)}
    {admin&&<Button startIcon={<Plus size={15}/>} onClick={()=>{mappingDraft.markEdited();setPredicates(v=>[...v,{source_id:'',column:'',op:'eq',value_kind:'text',value_text:''}]);setBindingDirty(true)}}>코드·조건 추가</Button>}
    <TextField fullWidth label="DB 계산식 (참고용)" value={binding.expression} multiline minRows={2} onChange={e=>mapChange({expression:e.target.value})}/>
    <TextField fullWidth label="매핑 설명·주의사항" value={binding.notes} multiline minRows={2} onChange={e=>mapChange({notes:e.target.value})}/>
   </fieldset>
   {mappingIssues.length>0&&<Alert severity="warning" className="knowledge-validation"><strong>매핑 확인 사항</strong><ul>{mappingIssues.map((v,i)=><li key={i}>{v}</li>)}</ul></Alert>}
   {checkResult&&<p className="knowledge-help">{checkResult.basis==='live_schema'?'현재 DB 스키마':'최근 탐색한 스키마'} 기준입니다. SQL은 실행하지 않았습니다.</p>}
   {admin&&<div className="knowledge-actions">
    <Button variant="outlined" startIcon={<Save size={16}/>} disabled={mappingBlocked||dirty||!bindingDirty||!binding.sources.length} onClick={saveMapping}>매핑 초안 저장</Button>
    {item.binding?.status==='draft'&&<Button disabled={mappingBlocked||dirty||bindingDirty||item.status!=='approved'} onClick={()=>{setReview({target:'binding',decision:'approve'})}}>매핑 승인</Button>}
    {item.binding&&item.binding.status!=='retired'&&<Button color="inherit" disabled={mappingBlocked||dirty||bindingDirty} onClick={()=>{setReview({target:'binding',decision:'retire'})}}>매핑 폐기</Button>}
   </div>}
  </>}
  {!item&&<p className="knowledge-help">초안을 저장한 뒤 현재 DB의 매핑을 추가할 수 있습니다. 빈 업무 정의는 자동으로 채워지지 않습니다.</p>}
  {review&&item&&<KnowledgeReviewDialog key={`${item.id}:${item.version}:${review.target}:${review.decision}`} item={item} review={review} active={active} busy={busy} error={error} blocked={definitionBlocked||review.target==='binding'&&mappingBlocked} close={()=>setReview(null)} submit={submitReview}/>}
  <DraftConflictDialog open={active&&!!conflict} title={conflict==='binding'?'매핑 변경 내용 비교':'정의 변경 내용 비교'} mine={conflict==='binding'?mappingValue:definitionComparison(definitionValue)} latest={conflict==='binding'?latestMapping:definitionComparison(latestDefinition)} onClose={()=>setConflict(null)} onUseLatest={()=>resolveConflict(false)} onKeepMine={conflict==='definition'||mappingCanRebase(mappingBase,latestItem)?()=>resolveConflict(true):undefined}/>
  <Dialog open={active&&!!history} onClose={()=>setHistory(null)} fullWidth maxWidth="sm"><DialogTitle>정의 버전 이력</DialogTitle><DialogContent>
   {history?.map(v=><button className="knowledge-history-row" key={v.version} onClick={()=>{setHistorical(v);setHistory(null)}}>
    <strong>v{v.version} · {statusNames[v.status]}</strong><span>{v.content.change_note||'변경 이유 미기재'}</span>
    <small>{new Date(v.created_at).toLocaleString()} {v.review_note?`· ${v.review_note}`:''}</small></button>)}
  </DialogContent><DialogActions><Button onClick={()=>setHistory(null)}>닫기</Button></DialogActions></Dialog>
  <Dialog open={active&&!!bindingHistory} onClose={()=>setBindingHistory(null)} fullWidth maxWidth="sm"><DialogTitle>현재 DB의 매핑 이력</DialogTitle><DialogContent>
   {bindingHistory?.map(v=><article className="knowledge-evidence" key={v.version}>
    <strong>매핑 v{v.version} · {statusNames[v.status]}</strong><p>{v.content.notes||'매핑 설명 없음'}</p>
    <p>{v.content.sources.map(r=>{const s=sources.find(s=>s.id===r.source_id);return `${r.role?`${r.role}: `:''}${s?`${s.schema_name}.${s.table_name}`:r.source_id} (${r.columns.join(', ')||'모든 컬럼'})`}).join(' · ')}</p>
    <small>{new Date(v.created_at).toLocaleString()} · {v.review_note||'검토 전'}</small>
   </article>)}
  </DialogContent><DialogActions><Button onClick={()=>setBindingHistory(null)}>닫기</Button></DialogActions></Dialog>
 </section>;
}

function KnowledgeReviewDialog({item,review,active,busy,blocked,error,close,submit}:{item:KnowledgeItem;review:{target:'definition'|'binding';decision:'approve'|'retire'};active:boolean;busy:boolean;blocked:boolean;error:unknown;close:()=>void;submit:(note:string,onSuccess:()=>void)=>Promise<void>}){
 const [note,setNote]=useState('');
 const base=review.target==='binding'?mappingVersion(item):definitionVersion(item);
 const [originalBase,setOriginalBase]=useState(base);
 const draft=useLocalDraft({kind:'knowledge-review-note',targetId:`${item.id}:${item.version}:${review.target}:${review.decision}`,projectId:null,baseVersion:originalBase,value:note,validate:(v)=>typeof v==='string'});
 const stale=originalBase!==base;
 return <Dialog open={active} onClose={()=>!busy&&close()} fullWidth maxWidth="sm"><DialogTitle>{review.target==='binding'?'매핑':'업무 정의'} {review.decision==='approve'?'승인':'폐기'}</DialogTitle>
  <DialogContent><p>{review.decision==='approve'?'담당자가 업무 의미와 적용 조건을 검토한 버전만 승인하세요. 기술 검증과 업무 정답 검토는 별개입니다.':'이 버전은 이후 지식 조회에 사용되지 않습니다. 과거 기록은 보존됩니다.'}</p>
   <LocalDraftNotice disabled={busy} active={active} draft={draft} onRestore={record=>{setNote(record.value);setOriginalBase(String(record.baseVersion))}}/>
   {stale&&<Alert severity="warning">이 사유를 작성한 이후 검토 상태가 바뀌었습니다. 창을 닫고 최신 버전을 확인한 뒤 다시 검토하세요.</Alert>}
   <TextField disabled={busy} autoFocus fullWidth required multiline minRows={3} label="검토 근거·사유" value={note} onChange={e=>{draft.markEdited();setNote(e.target.value)}}/><ErrorNotice error={error}/></DialogContent>
  <DialogActions><Button disabled={busy} onClick={close}>취소</Button><Button variant="contained" disabled={busy||blocked||stale||draft.blocked||!note.trim()} onClick={()=>void submit(note,draft.clearSaved)}>{review.decision==='approve'?'검토 후 승인':'폐기'}</Button></DialogActions>
 </Dialog>;
}
