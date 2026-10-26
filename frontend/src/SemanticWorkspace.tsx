import {useEffect,useState} from 'react';
import {Alert,Button,Chip,FormControlLabel,Checkbox,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {BookOpen,Check,ChevronRight,FileCheck2,GitBranch,Plus,Search,Sigma} from 'lucide-react';
import {api,post} from './api';
import {ErrorNotice,Loading} from './components';
import SemanticEditor from './SemanticEditor';
import SemanticCollection from './SemanticCollection';
import EvaluationRegistration from './EvaluationRegistration';
import KnowledgeWatchWorkspace from './KnowledgeWatchWorkspace';
import type {Project} from './types';
import type {AnalysisKnowledgeJourney} from './analysisSelection';
import type {ApprovedAnalysisDefinition} from './analyticsTypes';
import {evaluationScopeLabel} from './evaluationKnowledge';
import {kindNames,statusNames} from './semanticTypes';
import type {DefinitionRef,KnowledgeItem,KnowledgeKind,KnowledgeList,SemanticCatalog,SemanticContext} from './semanticTypes';
import './semantic.css';

const sections=[
 {id:'term',label:'업무 용어',icon:BookOpen,description:'같은 뜻으로 쓰이는 이름과 실제 업무 의미를 모읍니다.'},
 {id:'metric',label:'지표 정의',icon:Sigma,description:'계산식, 단위, 기간과 제외 조건을 명확하게 관리합니다.'},
 {id:'data',label:'데이터 설명·관계',icon:GitBranch,description:'한 행의 의미와 연결키, 중복 집계 주의사항을 기록합니다.'},
 {id:'query',label:'검증된 질의',icon:FileCheck2,description:'담당자가 검토한 질문과 SQL, 그 전제를 보존합니다.'},
 {id:'evaluation',label:'평가 사례',icon:BookOpen,description:'공정·Recipe 평가에서 관측한 변화와 적용 조건을 모아, 관련 데이터의 추가 분석에 참고합니다.'},
] as const;

export default function SemanticWorkspace({workspace,admin,projects,initialArea='knowledge',initialProjectFilter='all',initialQuestionId,entryToken,journey,onReturn,onJourneyDefinition,onJourneyInvalid,returning=false,active=true}:{workspace:string;admin:boolean;projects:Project[];initialArea?:'knowledge'|'inbox';initialProjectFilter?:string;initialQuestionId?:string|null;entryToken?:number;journey?:AnalysisKnowledgeJourney|null;onReturn?:()=>void;onJourneyDefinition?:(definition:ApprovedAnalysisDefinition)=>void;onJourneyInvalid?:()=>void;returning?:boolean;active?:boolean}){
 const [area,setArea]=useState<'knowledge'|'inbox'>(initialArea);
 useEffect(()=>{setArea(initialArea)},[initialArea,entryToken]);
 const client=useQueryClient(),[section,setSection]=useState<KnowledgeKind>('term'),[filter,setFilter]=useState('');
 const [scope,setScope]=useState('all'),[status,setStatus]=useState('all');
 const [selection,setSelection]=useState<KnowledgeItem|null|undefined>(),[editorRevision,setEditorRevision]=useState(0);
 const [searchOpen,setSearchOpen]=useState(false),[query,setQuery]=useState(''),[domain,setDomain]=useState('');
 const [result,setResult]=useState<SemanticContext|null>(null),[searching,setSearching]=useState(false),[error,setError]=useState<unknown>(null);
 const [keepContext,setKeepContext]=useState(false),[choices,setChoices]=useState<DefinitionRef[]>([]);
 const [evaluationOpen,setEvaluationOpen]=useState(false),[registeredQuestionId,setRegisteredQuestionId]=useState<string|null>(null);
 const [watchOpen,setWatchOpen]=useState(false);
 const [datasetSetup,setDatasetSetup]=useState<{id:string;entry:number}|null>(null);
 function openDatasetSetup(id:string){setDatasetSetup({id,entry:Date.now()});setSection('evaluation');setWatchOpen(true);setArea('knowledge')}
 const [setupRun,setSetupRun]=useState<{id:string;project_id:number|null;entry:number}|null>(null);
 function openSetup(id:string,project_id:number|null){setSetupRun({id,project_id,entry:Date.now()});setSection('evaluation');setWatchOpen(true);setArea('knowledge')}
 useEffect(()=>{setRegisteredQuestionId(initialQuestionId??null)},[initialQuestionId,entryToken]);
 const list=useQuery({queryKey:['semantic','items',workspace],queryFn:({signal})=>api<KnowledgeList>('/semantic/items',{signal})});
 const catalog=useQuery({queryKey:['semantic','catalog',workspace],queryFn:({signal})=>api<SemanticCatalog>('/semantic/catalog',{signal})});
 const items=list.data?.items??[],activeSection=sections.find(s=>s.id===section)!;
 const visible=items.filter(i=>(section==='data'?['data','relation'].includes(i.kind):i.kind===section)&&
  (scope==='all'||i.scope===scope)&&(status==='all'||i.status===status)&&
  `${i.content.name} ${i.content.domain} ${i.content.aliases.join(' ')}`.toLocaleLowerCase().includes(filter.toLocaleLowerCase()));
 const count=(kind:string)=>items.filter(i=>kind==='data'?['data','relation'].includes(i.kind):i.kind===kind).length;
 async function saved(item:KnowledgeItem){
  await client.invalidateQueries({queryKey:['semantic','items',workspace]});
  setSelection(item);setEditorRevision(v=>v+1);
 }
 async function search(selected=choices){
  if(!catalog.data)return;
  setSearching(true);setError(null);
  try{const value=await post<SemanticContext>('/semantic/context',{
   query,domain,connection_key:catalog.data.connection_key,workspace_token:workspace,project_id:null,
   selected_definitions:selected,...(keepContext&&result?{previous_context_id:result.context_id}:{})
  });setResult(value);setChoices(selected)}catch(e){setError(e)}finally{setSearching(false)}
 }
 return <div className="knowledge-workspace">
  <div className="knowledge-heading"><div><span className="knowledge-eyebrow">SHARED KNOWLEDGE</span><h1>AI 지식<span className="knowledge-shared-badge">모든 프로젝트 공통</span></h1>
   <p>모든 프로젝트가 함께 사용하는 지식 창고입니다. 업무의 의미를 정의하고, 데이터와 연결하세요.</p></div>
   <Button variant={searchOpen?'contained':'outlined'} startIcon={<Search size={16}/>} onClick={()=>{setArea('knowledge');setSearchOpen(!searchOpen)}}>검색 시험</Button>
  </div>
  <nav className="knowledge-area-nav" aria-label="AI 지식 관리"><button aria-current={area==='knowledge'?'page':undefined} onClick={()=>setArea('knowledge')}>지식 목록</button><button aria-current={area==='inbox'?'page':undefined} onClick={()=>setArea('inbox')}>확인 요청</button></nav>
  {journey&&<Alert severity="info" sx={{mb:2}} action={<Button disabled={returning} onClick={onReturn}>{returning?'조건 확인 중…':'원래 분석 조건으로 돌아가기'}</Button>}><strong>분석 기준 확인</strong><div>{journey.questionTitle}</div><small>답변과 승인된 설명을 확인한 후, 원래 조건에서 미정 값을 직접 입력하고 분석을 실행합니다.</small></Alert>}
  <div hidden={area==='knowledge'}><SemanticCollection workspace={workspace} admin={admin} items={items} projects={projects} initialProjectFilter={initialProjectFilter} entryToken={entryToken} journey={journey} registeredQuestionId={registeredQuestionId} onJourneyDefinition={onJourneyDefinition} onJourneyInvalid={onJourneyInvalid} active={active&&area!=='knowledge'} onKnowledge={()=>setArea('knowledge')} onOpenSetup={openSetup} onOpenDatasetSetup={openDatasetSetup}/></div>
  <div hidden={area!=='knowledge'}>
  <div className="knowledge-summary"><span><strong>{items.length}</strong> 지식 항목</span><span><strong>{items.filter(i=>i.published_version).length}</strong> 승인 버전 보유</span>
   <span><strong>{items.filter(i=>i.validation.definition_issues.length||i.validation.binding_issues.length).length}</strong> 확인 필요</span>
   <span className="knowledge-summary-note">공통 정의 + 현재 DB의 매핑</span></div>
  {!admin&&<Alert severity="info" sx={{mb:2}}>승인된 지식을 조회하고, 평가 사례를 등록하거나 확인 요청에서 업무 답변을 남길 수 있습니다. 지식 검토·승인은 관리자가 진행합니다.</Alert>}
  <ErrorNotice error={list.error??catalog.error}/>
  {searchOpen&&<section className="knowledge-search" aria-label="지식 검색 시험">
   <div><h3>질문에 어떤 지식이 제공되나요?</h3><p>승인된 정의와 현재 DB에서 사용할 수 있는 매핑을 조회합니다. SQL을 실행하거나 채팅에 적용하지 않습니다.</p></div>
   <form onSubmit={e=>{e.preventDefault();void search([])}}><div className="knowledge-search-fields">
    <TextField label="업무 질문" fullWidth value={query} onChange={e=>{setQuery(e.target.value);setChoices([])}} placeholder="실제 업무 질문을 입력하세요"/>
    <TextField label="업무 영역 (선택)" value={domain} onChange={e=>{setDomain(e.target.value);setChoices([])}}/>
    <Button variant="contained" type="submit" disabled={searching||!query.trim()||!catalog.data?.connection_key} startIcon={<Search size={16}/>}>조회</Button>
   </div>{result&&<FormControlLabel control={<Checkbox checked={keepContext} onChange={e=>setKeepContext(e.target.checked)}/>} label="이전 조회에서 확정한 정의와 조건 유지"/>}</form>
   <ErrorNotice error={error}/>
   {searching?<Loading label="관련 지식을 확인하는 중"/>:result&&<div className="knowledge-search-result">
    <Alert severity={result.resolution==='resolved'?'success':result.resolution==='needs_clarification'?'warning':'info'}>
     {result.resolution==='resolved'?'관련 지식을 찾았습니다. 실제 SQL과 업무 결과는 별도로 검증해야 합니다.':result.resolution==='needs_clarification'?'업무 의미를 확인해야 합니다.':'충분한 승인 지식이 없거나 매핑 검토가 필요합니다.'}</Alert>
    {result.clarifications.map(c=><div className="knowledge-clarification" key={c.id}><strong>{c.reason}</strong>
     <div>{c.options.map(o=><Button key={o.item_id} variant="outlined" size="small" onClick={()=>void search([...choices.filter(v=>!c.options.some(x=>x.item_id===v.item_id)),{item_id:o.item_id,version:o.version}])}>
      {o.name} · {o.domain||'업무 영역 미지정'} · v{o.version}</Button>)}</div></div>)}
    {result.gaps.map((g,i)=><p className="knowledge-help" key={i}>{g.reason}</p>)}
    {result.warnings.map((w,i)=><Alert severity="info" key={i}>{w}</Alert>)}
    {result.items.map(entry=><article className="knowledge-evidence" key={`${entry.id}:${entry.version}`}>
     <div><Chip size="small" label={kindNames[entry.kind]}/><strong>{entry.content.name}</strong><span>v{entry.version}{entry.binding?` · 매핑 v${entry.binding.version}`:''}</span></div>
     <p>{entry.content.description}</p>
     {entry.kind==='metric'&&<dl>{[['formula','계산식'],['grain','계산 단위'],['unit','출력 단위'],['exclusions','제외 조건'],['aggregation','재집계']].map(([f,l])=><div key={f}><dt>{l}</dt><dd>{String(entry.content.details[f]??'')}</dd></div>)}</dl>}
     {entry.kind==='query'&&<pre>{String(entry.content.details.sql??'')}</pre>}
     {entry.binding&&<p className="knowledge-help">{entry.binding.content.sources.map(r=>{const s=catalog.data?.sources.find(s=>s.id===r.source_id);return `${s?`${s.schema_name}.${s.table_name}`:r.source_id}${r.columns.length?` (${r.columns.join(', ')})`:''}`}).join(' · ')}</p>}
     <small>{entry.match_reasons.join(' · ')}</small>
    </article>)}
   </div>}
  </section>}
  <div className="knowledge-tabs" role="tablist" aria-label="지식 종류">{sections.map(s=><button key={s.id} role="tab" aria-selected={section===s.id} className={section===s.id?'active':''}
   onClick={()=>{setSection(s.id);setSelection(undefined);setFilter('')}}><s.icon size={17}/>{s.label}<span>{count(s.id)}</span></button>)}</div>
  {section==='evaluation'&&<details open={watchOpen} onToggle={e=>setWatchOpen(e.currentTarget.open)} style={{margin:'16px 0'}}><summary>지식을 바탕으로 새 데이터 지속 분석</summary>{watchOpen&&<KnowledgeWatchWorkspace requestedDataset={datasetSetup} requestedRun={setupRun} workspace={workspace} items={items} projects={projects} active={active&&area==='knowledge'} onQuestion={id=>{setRegisteredQuestionId(id);setArea('inbox')}}/>}</details>}
  <div className="knowledge-toolbar"><div><h3>{activeSection.label}</h3><p>{activeSection.description}</p></div>
   {section==='evaluation'?<Button variant="contained" startIcon={<Plus size={17}/>} onClick={()=>setEvaluationOpen(true)}>평가 사례 등록</Button>:admin&&<Button variant="contained" startIcon={<Plus size={17}/>} onClick={()=>{setSelection(null);setEditorRevision(v=>v+1)}}>새 지식</Button>}</div>
  {section==='evaluation'&&<Alert severity="info" sx={{mb:2}}>사례는 선택한 공통·프로젝트 범위와 평가 당시 조건을 함께 참고합니다. 승인된 사례는 AI 채팅에서 선택한 데이터와 연결해 찾고, 추가 분석을 제안하는 데 사용합니다. 실제 데이터의 재검증이나 원인 확인 여부는 별도로 구분합니다.</Alert>}
  <div className="knowledge-filters">
   <TextField size="small" label={section==='evaluation'?'사례 이름 검색':'이름·별칭·업무 영역 검색'} value={filter} onChange={e=>setFilter(e.target.value)}/>
   <TextField size="small" select label="범위" value={scope} onChange={e=>setScope(e.target.value)}><MenuItem value="all">모든 범위</MenuItem><MenuItem value="common">공통 정의</MenuItem><MenuItem value="database">현재 DB 전용</MenuItem></TextField>
   <TextField size="small" select label="상태" value={status} onChange={e=>setStatus(e.target.value)}><MenuItem value="all">모든 상태</MenuItem>{Object.entries(statusNames).map(([v,l])=><MenuItem value={v} key={v}>{l}</MenuItem>)}</TextField>
  </div>
  <div className={`knowledge-body ${selection!==undefined?'with-detail':''}`}>
   <div className="knowledge-list" role="region" aria-label="지식 목록">
    {list.isPending?<Loading/>:!visible.length?<div className="knowledge-empty"><activeSection.icon size={32}/><h3>{filter||scope!=='all'||status!=='all'?'조건에 맞는 지식이 없습니다':'아직 등록한 지식이 없습니다'}</h3>
     <p>{section==='evaluation'?'평가한 공정과 관측한 변화를 자연어로 등록하세요. 승인 전 내용은 확인 요청에서 확인합니다.':admin?'합의된 내용부터 초안으로 기록하세요. 미정인 업무 정의는 빈 상태로 남겨둘 수 있습니다.':'관리자가 업무 정의를 검토하고 승인하면 여기에 표시됩니다.'}</p>
     {section==='evaluation'&&!filter?<Button startIcon={<Plus size={15}/>} onClick={()=>setEvaluationOpen(true)}>첫 평가 사례 등록</Button>:admin&&!filter&&<Button startIcon={<Plus size={15}/>} onClick={()=>{setSelection(null);setEditorRevision(v=>v+1)}}>첫 지식 작성</Button>}
    </div>:visible.map(item=><button className={`knowledge-list-item ${selection?.id===item.id?'selected':''}`} key={item.id} onClick={()=>{setSelection(item);setEditorRevision(v=>v+1)}}>
     <div className="knowledge-list-item-top"><span className={`knowledge-status ${item.status}`}><Check size={12}/>{statusNames[item.status]}</span><span>{kindNames[item.kind]} · v{item.version}</span></div>
     <div className="knowledge-list-item-title"><strong>{item.content.name}</strong><ChevronRight size={17}/></div>
     <p>{item.content.description||'업무 설명 미정'}</p>
     <div className="knowledge-list-item-meta"><span>{item.scope==='common'?'공통 정의':'현재 DB'}</span>{item.kind!=='evaluation'&&item.content.domain&&<span>{item.content.domain}</span>}
      {item.evaluation_context&&<span>{evaluationScopeLabel(item.evaluation_context.dataset??item.evaluation_context.case_context??{project_id:null},projects).replace('공통 데이터셋','공통')} · {item.evaluation_context.dataset?.name??'자연어 평가 사례'}</span>}
      {(item.validation.definition_issues.length>0||item.validation.binding_issues.length>0)&&<span className="needs-review">확인 필요</span>}</div>
    </button>)}
   </div>
   {selection!==undefined&&<SemanticEditor key={`${selection?.id??'new'}-${editorRevision}`} item={selection} initialKind={section} items={items} sources={catalog.data?.sources??[]} projects={projects} admin={admin} active={active&&area==='knowledge'} onSaved={saved} onClose={()=>setSelection(undefined)} onEvaluationQuestion={id=>{setRegisteredQuestionId(id);setArea('inbox')}}/>}
  </div>
 </div>
 {evaluationOpen&&<EvaluationRegistration workspace={workspace} projects={projects} active={active} close={()=>setEvaluationOpen(false)} saved={async result=>{setEvaluationOpen(false);setRegisteredQuestionId(result.question_id);setArea('inbox');await client.invalidateQueries({queryKey:['semantic']})}}/>}
 </div>;
}
