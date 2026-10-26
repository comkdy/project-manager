import {useCallback,useEffect,useReducer,useRef,useState} from 'react';
import {Alert,Button,Checkbox,Chip,Dialog,DialogActions,DialogContent,DialogTitle,FormControlLabel,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Check,ChevronDown,Clock3,Inbox,MessageSquare,Plus,Settings2,Sparkles} from 'lucide-react';
import {api,ApiError,post} from './api';
import EvaluationKnowledgeSummary from './EvaluationKnowledgeSummary';
import {ErrorNotice,Loading} from './components';
import {detailFields,kindNames} from './semanticTypes';
import type {KnowledgeItem} from './semanticTypes';
import type {Project} from './types';
import {questionStatuses,topics,questionProjectId} from './semanticCollectionTypes';
import type {CollectedDraft,CollectionDataset,CollectionSettings,KnowledgeQuestion,QuestionTarget} from './semanticCollectionTypes';
import {confirmationInProject,confirmationLabels,confirmationMatches,confirmationStep} from './confirmationRequests';
import {questionTitleParts} from './semanticQuestionTitle';
import {draftTargetChanged,draftReviewChanged,draftReviewInput,draftReviewReducer,initialDraftReview} from './draftReviewState';
import type {DraftReviewState} from './draftReviewState';
import {analysisReturnValid,exactQuestionDraft,questionDraftTarget,type AnalysisKnowledgeJourney} from './analysisSelection';
import type {ApprovedAnalysisDefinition} from './analyticsTypes';
import {loadAnalysisRun} from './analyticsApi';
import {DatasetLabel} from './DatasetScopeBadge';
import {DraftConflictDialog,LocalDraftNotice,useLocalDraft} from './localDraft';
import {blankQuestionInput,questionCanAnswer,validAnswer,validNewQuestionInput,validReviewInput} from './semanticDrafts';
import type {AnswerInput,NewQuestionInput} from './semanticDrafts';
import './semantic-collection.css';

const base='/semantic/collection';
const date=(value:string|null)=>value?new Date(value).toLocaleString('ko-KR'):'—';

function ProjectTag({id,projects}:{id:number|null;projects:Project[]}){
 return <Chip className="collection-project-tag" size="small" variant="outlined" label={id===null?'공통':projects.find(p=>p.id===id)?.name??`프로젝트 #${id}`}/>;
}

function QuestionContext({question:q,projects,status=q.status,kind}:{question:KnowledgeQuestion;projects:Project[];status?:string;kind?:string}){
 return <header className="collection-question-context"><div className="collection-question-meta">{q.dataset&&<span className="collection-dataset-tag">데이터셋 <b>{q.dataset.name}</b><small>v{q.dataset.version}</small></span>}<ProjectTag id={questionProjectId(q)} projects={projects}/>{kind&&<small>{kind}</small>}</div><span className={`collection-state ${status}`}>{status===q.status?confirmationLabels[confirmationStep(q)]:questionStatuses[status]}</span></header>;
}

function QuestionText({question}:{question:KnowledgeQuestion}){
 return <>{questionTitleParts(question).map((part,index)=>part.column?<span key={index} className="collection-inline-column" title={part.sourceName?`원천: ${part.sourceName}`:undefined}>{part.text}</span>:part.text)}</>;
}

function TargetBadges({targets,label='질문 대상'}:{targets:QuestionTarget[];label?:string}){
 return targets.length?<ul className="collection-targets" aria-label={label}>{targets.map((target,i)=><li key={i}>
  {target.kind==='dataset'?'데이터셋 전체':<><span>{target.kind==='column_value'?'컬럼 값':target.source_id?'원천 컬럼':'컬럼'}</span> <b>{target.column}</b>{target.kind==='column_value'&&<> = <code>“{target.value}”</code></>}{target.source_name&&<small> · {target.source_name}</small>}</>}
 </li>)}</ul>:null;
}

export default function SemanticCollection({workspace,admin,items,onKnowledge,projects,initialProjectFilter='all',entryToken,journey,registeredQuestionId,onJourneyDefinition,onJourneyInvalid,onOpenDatasetSetup,onOpenSetup,active=true}:{workspace:string;admin:boolean;items:KnowledgeItem[];onKnowledge:()=>void;projects:Project[];initialProjectFilter?:string;entryToken?:number;journey?:AnalysisKnowledgeJourney|null;registeredQuestionId?:string|null;onJourneyDefinition?:(definition:ApprovedAnalysisDefinition)=>void;onJourneyInvalid?:()=>void;onOpenDatasetSetup?:(id:string,projectId:number|null)=>void;onOpenSetup?:(id:string,projectId:number|null)=>void;active?:boolean}){
 const client=useQueryClient(),[filter,setFilter]=useState('pending'),[text,setText]=useState('');
 const [selectedQuestion,setSelectedQuestion]=useState<string|null>(null),[answers,setAnswers]=useState<Record<string,AnswerInput>>({});
 const reviewStates=useRef<Record<string,DraftReviewState>>({});
 const rememberReview=useCallback((id:string,state:DraftReviewState)=>{reviewStates.current[id]=state},[]);
 const [projectFilter,setProjectFilter]=useState(initialProjectFilter);
 const [settingsOpen,setSettingsOpen]=useState(false),[newOpen,setNewOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[message,setMessage]=useState('');
 const questionScope=projectFilter==='all'?'':projectFilter==='shared'?'?shared_only=true':`?project_id=${encodeURIComponent(projectFilter)}`;
 const questions=useQuery({queryKey:['semantic','questions',workspace,projectFilter],queryFn:()=>api<KnowledgeQuestion[]>(`${base}/questions${questionScope}`),enabled:active,refetchInterval:active?5000:false});
 const config=useQuery({queryKey:['semantic','collection-settings',workspace],queryFn:()=>api<CollectionSettings>(`${base}/settings`),enabled:admin&&active,refetchInterval:active?15000:false});
 const focused=useQuery({queryKey:['semantic','question',workspace,journey?.questionId],queryFn:()=>api<KnowledgeQuestion>(`${base}/questions/${encodeURIComponent(journey!.questionId)}`),enabled:!!journey&&active,refetchInterval:active?5000:false,retry:false});
 const registered=useQuery({queryKey:['semantic','question',workspace,registeredQuestionId],queryFn:()=>api<KnowledgeQuestion>(`${base}/questions/${encodeURIComponent(registeredQuestionId!)}`),enabled:!!registeredQuestionId&&active,refetchInterval:active?5000:false,retry:false});
 const origin=useQuery({queryKey:['analytics','knowledge-return',workspace,journey?.runId,entryToken,focused.data?.revision],queryFn:()=>loadAnalysisRun(journey!.runId),enabled:!!journey&&active&&!!focused.data,retry:false});
 const definition=origin.data?.questions.find(q=>q.id===journey?.questionId)?.approved_definition;
 useEffect(()=>{
  if(!journey||!active)return;
  if(focused.isError||focused.data?.dataset_changed||origin.isError||origin.data&&!analysisReturnValid(journey,origin.data)){onJourneyInvalid?.();return}
  if(definition&&!journey.definition)onJourneyDefinition?.(definition);
 },[journey,active,focused.isError,focused.data?.dataset_changed,origin.isError,origin.data,definition,onJourneyDefinition,onJourneyInvalid]);
 async function refresh(){await Promise.all([client.invalidateQueries({queryKey:['semantic']}),client.invalidateQueries({queryKey:['analytics']}),client.invalidateQueries({queryKey:['evaluation-analysis']})])}
 async function run(action:()=>Promise<unknown>){setBusy(true);setError(null);setMessage('');try{await action();await refresh()}catch(e){setError(e)}finally{setBusy(false)}}
 useEffect(()=>{setProjectFilter(initialProjectFilter);setFilter('pending');setText('')},[initialProjectFilter,entryToken]);
 useEffect(()=>{setMessage('');setError(null)},[registeredQuestionId,journey?.questionId,entryToken]);
 useEffect(()=>{setSelectedQuestion(null);setError(null)},[projectFilter]);
 const openedEntry=useRef('');
 useEffect(()=>{
  const q=journey?focused.data:registered.data,key=`${entryToken??''}:${q?.id??''}`;
  if(!q||openedEntry.current===key)return;
  openedEntry.current=key;
  if(!confirmationInProject(q,projectFilter))setProjectFilter(questionProjectId(q)===null?'shared':String(questionProjectId(q)));
 },[entryToken,journey?.questionId,focused.data,registered.data,projectFilter]);
 const showRegistered=!!registeredQuestionId&&registeredQuestionId!==journey?.questionId&&(!registered.data||confirmationInProject(registered.data,projectFilter));
 const showJourney=!!journey&&(!focused.data||confirmationInProject(focused.data,projectFilter));
 const rows=(questions.data??[]).filter(q=>confirmationMatches(q,filter,selectedQuestion===q.id)&&`${q.title} ${q.dataset?.name??''}`.toLocaleLowerCase().includes(text.toLocaleLowerCase())&&!(showJourney&&q.id===journey?.questionId)&&!(showRegistered&&q.id===registeredQuestionId));
 const cardProps=(q:KnowledgeQuestion)=>({workspace,projects,admin,items,busy,run,active,onOpenDatasetSetup,onOpenSetup,
  answer:answers[q.id]??{text:'',revision:null},setAnswer:(value:AnswerInput)=>setAnswers(previous=>({...previous,[q.id]:value})),
  reviewStates:reviewStates.current,rememberReview,onReviewed:(decision:'approve'|'reject')=>setMessage(decision==='approve'?'승인한 내용을 AI 지식에 반영했습니다. 이 항목에서 원문과 처리 결과를 계속 확인할 수 있습니다.':'보완이 필요해 다시 설명을 요청했습니다. 같은 항목에 답변을 남겨 주세요.')});
 return <section className="collection-workspace">
  <div className="collection-intro"><div><h2>확인 요청</h2><p>AI가 기다리는 확인을 한곳에서 처리하세요. 설명을 보완하면 같은 항목에서 정리된 내용을 검토·승인할 수 있습니다.</p></div>
   {admin&&<div className="collection-actions"><Button startIcon={<Settings2 size={16}/>} onClick={()=>setSettingsOpen(true)}>수집 설정</Button><Button startIcon={<Plus size={16}/>} onClick={()=>setNewOpen(true)}>질문 작성</Button>
    <Button variant="contained" startIcon={<Sparkles size={16}/>} disabled={busy||!config.data?.configured||!config.data?.datasets.length||config.data.last_status==='running'} onClick={()=>void run(async()=>{const r=await post<{status:string;created:number;message:string}>(`${base}/generate`,{});if(r.status==='error')throw new Error(r.message);setMessage(`${r.message} 새 질문 ${r.created}개`)})}>지금 질문 생성</Button></div>}
  </div>
  <ErrorNotice error={error??questions.error??config.error}/>
  {message&&<Alert className="confirmation-result" severity="success" sx={{mb:2}} action={<Button onClick={onKnowledge}>지식 목록에서 확인</Button>}>{message}</Alert>}
  {admin&&config.data&&<div className="collection-run-state"><Clock3 size={16}/><span>{config.data.enabled?`자동 수집 · ${config.data.interval_hours}시간마다 · 다음 ${date(config.data.next_run)}`:'자동 수집 꺼짐'}</span><span>{config.data.last_message}</span></div>}
  {admin&&config.data&&!config.data.configured&&<Alert severity="info" sx={{mb:2}}>공용 AI 연결 설정을 확인해 주세요. 연결이 준비되면 질문 생성과 답변 정리를 사용할 수 있습니다.</Alert>}
  <div className="collection-filters"><TextField label="요청·데이터셋 검색" value={text} onChange={e=>setText(e.target.value)}/><TextField select label="프로젝트" value={projectFilter} onChange={e=>setProjectFilter(e.target.value)}><MenuItem value="all">전체 프로젝트</MenuItem><MenuItem value="shared">공통 요청</MenuItem>{projects.map(p=><MenuItem key={p.id} value={String(p.id)}>{p.name}</MenuItem>)}</TextField><TextField select label="필요한 확인" value={filter} onChange={e=>setFilter(e.target.value)}><MenuItem value="pending">처리할 요청</MenuItem><MenuItem value="all">모든 요청</MenuItem>{Object.entries(confirmationLabels).map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField></div>
  {showRegistered&&<section aria-label="선택한 확인 요청">
   <Alert severity="info" sx={{mb:2}}>선택한 확인 요청입니다. 설명 보완부터 검토·승인까지 이 항목에서 이어집니다.</Alert>
   {registered.isPending?<Loading label="선택한 확인 요청을 확인하는 중"/>:registered.error?<ErrorNotice error={registered.error}/>:registered.data&&<QuestionCard question={registered.data} expanded toggle={()=>{}} {...cardProps(registered.data)}/>}
  </section>}
  {showJourney&&<section aria-label="분석에서 확인할 질문">
   {focused.isPending?<Loading label="연결된 질문을 확인하는 중"/>:focused.error?<ErrorNotice error={focused.error}/>:focused.data&&<QuestionCard question={focused.data} expanded toggle={()=>{}} {...cardProps(focused.data)}/>}
   {definition&&<Alert severity="success" sx={{my:2}}><strong>현재 승인된 기준 · {definition.content.name} · v{definition.version}</strong><p style={{whiteSpace:'pre-wrap'}}>{definition.content.description}</p>{Object.entries(definition.content.details??{}).filter(([,value])=>value!==null&&value!=='').map(([key,value])=><p key={key}>{String(value)}</p>)}<small>이 설명은 확인 근거입니다. 분석 조건 값은 돌아간 뒤 직접 입력하세요.</small></Alert>}
  </section>}
  <div className="collection-list collection-inbox">
   {questions.isPending?<Loading/>:!rows.length&&!showRegistered&&!showJourney?<div className="knowledge-empty"><Inbox size={30}/><h3>조건에 맞는 확인 요청이 없습니다</h3><p>필터를 변경하거나 새로운 질문·평가 사례를 등록해 주세요.</p></div>:
    rows.map(q=><QuestionCard key={q.id} question={q} expanded={selectedQuestion===q.id} toggle={()=>{setSelectedQuestion(selectedQuestion===q.id?null:q.id);setError(null)}} {...cardProps(q)}/>)}
  </div>
  {settingsOpen&&config.data&&<CollectionSettingsDialog active={active} value={config.data} close={()=>setSettingsOpen(false)} saved={refresh}/>}
  {newOpen&&config.data&&<NewQuestionDialog active={active} config={config.data} close={()=>setNewOpen(false)} saved={async(id)=>{setFilter('pending');setText('');await refresh();setSelectedQuestion(id)}}/>}
 </section>;
}

function QuestionCard({question:q,workspace,projects,expanded,toggle,busy,run,answer,setAnswer,admin,items,reviewStates,rememberReview,onReviewed,onOpenDatasetSetup,onOpenSetup,active=true}:{question:KnowledgeQuestion;workspace:string;projects:Project[];expanded:boolean;toggle:()=>void;busy:boolean;run:(fn:()=>Promise<unknown>)=>Promise<void>;answer:AnswerInput;setAnswer:(value:AnswerInput)=>void;admin:boolean;items:KnowledgeItem[];reviewStates:Record<string,DraftReviewState>;rememberReview:(id:string,state:DraftReviewState)=>void;onReviewed:(decision:'approve'|'reject')=>void;onOpenDatasetSetup?:(id:string,projectId:number|null)=>void;onOpenSetup?:(id:string,projectId:number|null)=>void;active?:boolean}){
 const detail=useQuery({queryKey:['semantic','question',workspace,q.id],queryFn:()=>api<KnowledgeQuestion>(`${base}/questions/${q.id}`),enabled:expanded&&active,refetchInterval:expanded&&active?5000:false});
 const current=detail.data&&detail.data.revision>=q.revision?detail.data:q;
 const panelId=`question-answer-${q.id}`,titleId=`question-title-${q.id}`;
 return <article data-question-id={q.id} className={`collection-question-card ${expanded?'expanded':''}`}>
  <button type="button" className="collection-question" aria-expanded={expanded} aria-controls={panelId} onClick={toggle}>
   <QuestionContext question={current} projects={projects} kind={kindNames[q.kind]}/>
   <span className="collection-question-title"><strong id={titleId}><QuestionText question={q}/></strong><ChevronDown size={18} className="collection-expand-icon" aria-hidden="true"/></span>
   <p>{q.reason}</p>{q.dataset_changed&&<small>질문 생성 후 데이터셋 변경됨</small>}
  </button>
  <div id={panelId} role="region" aria-labelledby={titleId} className="collection-answer-panel" hidden={!expanded}>
   {expanded&&(detail.isPending?<Loading/>:detail.error?<ErrorNotice error={detail.error}/>:detail.data&&<>
    {q.kind!=='evaluation'&&<ol className="confirmation-progress" aria-label="확인 진행 단계"><li className={confirmationStep(current)==='answer'?'current':''}>1. 설명 확인</li><li className={current.status==='structuring'?'current':''}>2. AI 정리</li><li className={confirmationStep(current)==='approval'?'current':''}>3. 검토·승인</li><li className={current.status==='answered'?'current':''}>4. 지식 반영</li></ol>}
    {detail.data.dataset_setup&&onOpenDatasetSetup&&<Alert severity="info" action={<Button onClick={()=>onOpenDatasetSetup(detail.data!.dataset_setup!.recommendation_id,detail.data!.dataset_setup!.project_id)}>데이터셋 추천으로 돌아가기</Button>}>분석할 데이터를 찾거나 구성하기 위한 질문입니다. 승인한 설명으로 추천을 이어갑니다.</Alert>}
    {detail.data.analysis_setup&&onOpenSetup&&<Alert severity="info" action={<Button onClick={()=>onOpenSetup(detail.data!.analysis_setup!.run_id,detail.data!.analysis_setup!.project_id)}>분석 추천으로 돌아가기</Button>}>이 설명은 최초 분석의 데이터 연결을 확인하기 위한 질문입니다. 승인된 답변으로 추천을 이어갑니다.</Alert>}
    <QuestionAnswer active={active&&expanded} question={detail.data} answer={answer} setAnswer={setAnswer} busy={busy} run={run} admin={admin}/>
    {admin&&<InlineQuestionReview workspace={workspace} question={detail.data} active={active&&expanded} items={items} projects={projects} busy={busy||!!answer.text} run={run} reviewStates={reviewStates} remember={rememberReview} onReviewed={onReviewed}/>}
   </>)}
  </div>
 </article>;
}

function QuestionAnswer({question:incoming,answer,setAnswer,busy,run,admin,active}:{question:KnowledgeQuestion;answer:AnswerInput;setAnswer:(value:AnswerInput)=>void;busy:boolean;run:(fn:()=>Promise<unknown>)=>Promise<void>;admin:boolean;active:boolean}){
 const [remote,setRemote]=useState<KnowledgeQuestion|null>(null),[compare,setCompare]=useState(false);
 const q=remote&&remote.revision>incoming.revision?remote:incoming;
 const baseRevision=answer.revision??q.revision;
 const draft=useLocalDraft({kind:'knowledge-answer',targetId:q.id,projectId:questionProjectId(q),baseVersion:baseRevision,value:answer.text,validate:validAnswer});
 const changed=!!answer.text&&baseRevision!==q.revision,canAnswer=questionCanAnswer(q);
 async function submit(){if(busy||!answer.text.trim()||!canAnswer||changed||draft.blocked)return;
  await run(async()=>{try{await post(`${base}/questions/${q.id}/answers`,{expected_revision:baseRevision,text:answer.text});draft.clearSaved();setAnswer({text:'',revision:null})}
   catch(error){if(error instanceof ApiError&&error.status===409){try{setRemote(await api<KnowledgeQuestion>(`${base}/questions/${q.id}`));setCompare(true)}catch{/* Keep the failed answer locally. */}}throw error}});
 }
 return <div className="collection-detail collection-inline-answer">
  {q.kind==='evaluation'&&<EvaluationUnderstanding question={q}/>}
  {q.review_feedback&&<p className="collection-review-feedback"><strong>보완 요청 내용</strong><br/>{q.review_feedback}</p>}
  {q.kind!=='evaluation'&&<ExecutionEvidence question={q}/>}
  {q.dataset_changed&&<Alert severity="warning" sx={{my:2}}>질문 생성 후 데이터셋이 변경되었습니다. 관리자가 최신 데이터셋으로 새 질문을 작성해야 합니다.</Alert>}
  {q.followups.length>0&&<div className="collection-followups"><strong>지금 확인할 내용</strong><ul>{q.followups.slice(0,2).map((f,i)=><li key={i}>{f}</li>)}</ul></div>}
  {!!q.answers?.length&&<details open={q.kind!=='evaluation'&&(q.status!=='review'||!admin)}><summary>{q.kind==='evaluation'?'등록 원문·답변 이력':'등록 내용·답변 이력'} ({q.answers.length})</summary><div className="collection-answers">{q.answers.map(a=><div key={a.id}><div className="collection-answer-by"><strong>{a.user_name}</strong><small>{date(a.created_at)}</small></div><TargetBadges targets={a.targets} label="답변 대상"/><p>{a.text}</p>{a.status==='error'&&<Alert severity="warning">{a.error}<Button size="small" disabled={busy} onClick={()=>void run(async()=>{await post(`${base}/answers/${a.id}/retry`,{})})}>자동 정리 재시도</Button></Alert>}</div>)}</div></details>}
  <LocalDraftNotice disabled={busy} active={active} draft={draft} onRestore={record=>setAnswer({text:record.value,revision:Number(record.baseVersion)})}/>
  {changed&&<Alert severity="warning" action={<Button onClick={()=>setCompare(true)}>답변 변경 내용 비교</Button>}>답변 작성 후 질문이나 답변이 변경되었습니다. 최신 내용을 확인하기 전에는 전송하지 않습니다.</Alert>}
  {!canAnswer&&answer.text&&<Alert severity="info" action={<Button onClick={()=>setCompare(true)}>보관 답변 확인</Button>}>입력한 답변을 보관했습니다. 현재 질문 상태에서는 전송할 수 없습니다.</Alert>}
  <DraftConflictDialog open={active&&compare} title="내 답변과 최신 저장 내용 비교" mine={answer.text} latest={{question:q.title,status:questionStatuses[q.status],answers:(q.answers??[]).map(a=>({text:a.text,user:a.user_name}))}} onClose={()=>setCompare(false)} onUseLatest={()=>{draft.discard();setAnswer({text:'',revision:null});setCompare(false)}} onKeepMine={canAnswer?()=>{draft.markEdited();setAnswer({...answer,revision:q.revision});setCompare(false)}:undefined}/>
  {!admin&&q.status==='review'&&<Alert severity="info" sx={{my:2}}>{q.followups.length?'아래 확인 내용을 보완하면 관리자가 같은 항목에서 검토·승인합니다.':'설명 정리가 끝났습니다. 관리자가 이 항목에서 검토·승인할 차례입니다.'}</Alert>}
  {q.status==='structuring'?<Alert severity="info">답변을 보관했습니다. 근거를 확인하며 초안을 만들고 있습니다.</Alert>:q.status==='answered'?<Alert severity="success">검토를 거쳐 지식 저장소에 반영했습니다.</Alert>:
   <details className="confirmation-answer-form" open={q.status!=='review'||q.followups.length>0||!!answer.text}><summary>{q.status==='review'&&!q.followups.length?'설명 보완하기 (선택)':'추가 설명 작성'}</summary><form onSubmit={e=>{e.preventDefault();void submit()}}>
    <TextField fullWidth multiline minRows={3} label={q.kind==='evaluation'?'답변 또는 수정할 내용':q.status==='review'?'추가 답변':'업무 답변'} helperText={q.execution_evidence?'평가 조건과 해석을 보완하세요. 계산된 수치와 실행 근거는 그대로 보존됩니다.':'질문에 대한 업무 내용을 설명하세요. 확실하지 않은 내용은 미정으로 남습니다.'} value={answer.text} onChange={e=>{draft.markEdited();setAnswer({text:e.target.value,revision:answer.revision??q.revision})}} inputProps={{maxLength:6000}} disabled={busy||q.dataset_changed}/>
    <div className="collection-actions"><Button variant="contained" startIcon={<MessageSquare size={16}/>} disabled={busy||draft.blocked||changed||!answer.text.trim()||!canAnswer} type="submit">{q.status==='review'?'추가 답변으로 초안 보완':'답변 보내고 초안 만들기'}</Button>
     {['open','deferred'].includes(q.status)&&<Button disabled={busy} onClick={()=>void run(async()=>{await post(`${base}/questions/${q.id}/defer`,{expected_revision:q.revision,days:q.status==='deferred'?0:7})})}>{q.status==='deferred'?'보류 해제':'모름 · 7일 보류'}</Button>}</div></form></details>}
 </div>;
}

function InlineQuestionReview({workspace,question,active,items,projects,busy,run,reviewStates,remember,onReviewed}:{workspace:string;question:KnowledgeQuestion;active:boolean;items:KnowledgeItem[];projects:Project[];busy:boolean;run:(fn:()=>Promise<unknown>)=>Promise<void>;reviewStates:Record<string,DraftReviewState>;remember:(id:string,state:DraftReviewState)=>void;onReviewed:(decision:'approve'|'reject')=>void}){
 const target=questionDraftTarget(question,true);
 const [held,setHeld]=useState<CollectedDraft|null>(null);
 const [finished,setFinished]=useState<string|null>(null);
 const linked=useQuery({queryKey:['semantic','linked-draft',workspace,target?.id],queryFn:async()=>{
  const draft=await api<CollectedDraft>(`${base}/drafts/${encodeURIComponent(target!.id)}`);
  if(!exactQuestionDraft(question.id,target!.answer_id,draft))throw new Error('이 요청과 연결된 초안을 확인할 수 없습니다.');
  return draft;
 },enabled:active&&!!target,refetchInterval:active&&target?5000:false,retry:false});
 const heldState=held?reviewStates[held.id]:undefined;
 const hasEdits=!!heldState&&(heldState.dirty||!!heldState.note||!!heldState.target);
 useEffect(()=>{if(linked.data&&linked.data.id!==finished&&(!held||held.id===linked.data.id||!hasEdits))setHeld(linked.data)},[linked.data,held?.id,hasEdits,finished]);
 const current=held?.id===linked.data?.id?linked.data:held;
 const unavailable=!target||current?.id!==target.id||linked.isError||current?.status!=='pending';
 if(!target&&!hasEdits)return null;
 return <section className="confirmation-review" aria-label="이 요청의 검토·승인">
  <ErrorNotice error={linked.error}/>
  {current&&current.id!==target?.id&&hasEdits&&<Alert severity="warning">답변 또는 처리 상태가 바뀌었습니다. 작성 중인 검토 내용은 보관했으며 이전 초안의 승인은 중지했습니다.{linked.data&&<Button disabled={busy} onClick={()=>setHeld(linked.data!)}>새 답변의 초안 보기</Button>}</Alert>}
  {current&&(current.id===target?.id||hasEdits)&&current.id!==finished?<DraftReview key={current.id} active={active} draft={current} initialState={reviewStates[current.id]} remember={remember} unavailable={unavailable} items={items} projects={projects} busy={busy} run={run} onApproved={decision=>{setFinished(current.id);setHeld(null);delete reviewStates[current.id];onReviewed(decision)}}/>:linked.isPending&&target?<Loading label="이 요청의 승인 초안을 확인하는 중"/>:null}
 </section>;
}

function ExecutionEvidence({question}:{question:KnowledgeQuestion}){
 const evidence=question.execution_evidence;
 return evidence?<Alert severity="info" sx={{my:2}}><strong>현재 데이터에서 계산한 결과</strong><p style={{whiteSpace:'pre-wrap'}}>{evidence.summary}</p><small>데이터셋 v{evidence.dataset_version} · 조회 {evidence.captured_at?date(evidence.captured_at):'확인 필요'}</small><p>수치는 실행 결과로 보존됩니다. 평가 조건과 해석을 검토한 뒤 승인하면 AI 지식에 반영됩니다.</p></Alert>:null;
}

function EvaluationUnderstanding({question:q}:{question:KnowledgeQuestion}){
 const value=q.interpretation;
 if(!value)return null;
 const parts=[value.description,...['operation','recipe_changes','inline_observations','epm_observations'].map(key=>value.details[key])]
  .filter((text):text is string=>typeof text==='string'&&!!text)
  .filter((text,index,all)=>!all.some((other,i)=>i<index&&other.includes(text)));
 return <section className="evaluation-understanding" aria-label="AI가 이해한 평가 내용"><h3>AI가 이해한 내용</h3>
  <EvaluationKnowledgeSummary details={value.details} terms={value.terms} execution={q.execution_evidence}/>
  <details><summary>원문 요약·적용 조건</summary>
   {!q.execution_evidence&&parts.map((text,index)=><p key={index} className="evaluation-observation">{text}</p>)}
   {['conditions','evidence','limitations'].map(key=>typeof value.details[key]==='string'&&value.details[key]?<p key={key}>{String(value.details[key])}</p>:null)}
   {!value.details.conditions&&<p>제품·장비·기간 등 평가 조건이 충분히 기록되지 않았습니다.</p>}
  </details>
 </section>;
}

function DraftReview({active,draft:incoming,initialState,remember,unavailable,items,projects,busy,run,onApproved}:{active:boolean;draft:CollectedDraft;initialState?:DraftReviewState;remember:(id:string,state:DraftReviewState)=>void;unavailable:boolean;items:KnowledgeItem[];projects:Project[];busy:boolean;run:(fn:()=>Promise<unknown>)=>Promise<void>;onApproved:(decision:'approve'|'reject')=>void}){
 const client=useQueryClient();
 const [state,dispatch]=useReducer(draftReviewReducer,initialState??initialDraftReview(incoming));
 useEffect(()=>{remember(incoming.id,state)},[incoming.id,state,remember]);
 useEffect(()=>{dispatch({type:'receive',draft:incoming})},[incoming]);
 const {proposal,unresolved,note,target,targetRevision,dirty}=state;
 const [compare,setCompare]=useState(false);
 const local=useLocalDraft({kind:'knowledge-collected-draft',targetId:incoming.id,projectId:questionProjectId(incoming.question),baseVersion:state.editRevision,value:draftReviewInput(state),validate:validReviewInput});
 const selectedTarget=items.find(i=>i.id===target),targetChanged=draftTargetChanged(state,items);
 const d={...state.base,question:state.latest.question,issues:state.latest.issues,conflicts:state.latest.conflicts};
 const changed=draftReviewChanged(state),blocked=busy||changed||targetChanged||unavailable||state.latest.status!=='pending'||local.blocked;
 const edit=(value:Parameters<typeof draftReviewReducer>[1]&{type:'edit'})=>{local.markEdited();dispatch(value)};
 const setNote=(note:string)=>edit({type:'edit',value:{note}});
 const setTarget=(target:string)=>edit({type:'edit',value:{target,targetRevision:items.find(i=>i.id===target)?.revision??null}});
 const content=proposal.content;
 function change(patch:Partial<typeof content>){edit({type:'edit',value:{proposal:{...proposal,content:{...content,...patch}},dirty:true}})}
 async function mutate(action:()=>Promise<unknown>){await run(async()=>{try{await action()}catch(error){if(error instanceof ApiError&&error.status===409){try{dispatch({type:'receive',draft:await api<CollectedDraft>(`${base}/drafts/${d.id}`)});await client.invalidateQueries({queryKey:['semantic','items']});setCompare(true)}catch{/* Leave local edits intact when refresh fails. */}}throw error}})}
 async function review(decision:'approve'|'reject'){if(blocked)return;await mutate(async()=>{await post(`${base}/drafts/${d.id}/review`,{expected_revision:state.editRevision,decision,note:note.trim()||(decision==='approve'?'원문과 AI가 정리한 보고 내용·용어 연결을 검토함':'보완 후 재검토 요청'),target_item_id:target||null,target_revision:targetRevision});local.clearSaved();onApproved(decision)})}
 if(proposal.kind==='evaluation'||d.question.analysis_setup||d.question.dataset_setup)return <article className="collection-detail evaluation-review" aria-label={(d.question.analysis_setup||d.question.dataset_setup)?'데이터 연결 설명 승인':'평가 사례 승인'}>
  {(d.question.analysis_setup||d.question.dataset_setup)&&<section><h3>AI가 정리한 연결 설명</h3><p style={{whiteSpace:'pre-wrap'}}>{proposal.content.description}</p></section>}
  <LocalDraftNotice disabled={busy} active={active} draft={local} onRestore={record=>dispatch({type:'restore',value:record.value,revision:Number(record.baseVersion)})}/>
  {(changed||targetChanged||dirty)&&<Alert severity="warning" action={<Button disabled={busy} onClick={()=>setCompare(true)}>변경 내용 확인</Button>}>검토 내용이 변경됐습니다. 최신 정리와 비교한 뒤 승인하세요.</Alert>}
  {unavailable&&<p role="status">새 답변이 처리되었거나 요청 상태가 바뀌었습니다. 최신 초안을 확인하고 있습니다.</p>}
  <DraftConflictDialog open={active&&compare} mine={draftReviewInput(state)} latest={state.latest.proposal} onClose={()=>setCompare(false)} onUseLatest={()=>{local.discard();dispatch({type:'reload'});setCompare(false)}}/>
  {!!d.issues.length&&<p className="knowledge-help">승인 전에 확인할 내용: {d.issues.join(' / ')}</p>}
  {!!d.conflicts.length&&<TextField fullWidth select label="기존 사례에 반영할 경우 선택" value={target} disabled={busy} onChange={e=>setTarget(e.target.value)}><MenuItem value="">새 사례</MenuItem>{items.filter(i=>d.conflicts.some(c=>c.id===i.id)).map(i=><MenuItem key={i.id} value={i.id}>{i.content.name} · 새 버전</MenuItem>)}</TextField>}
  <details><summary>검토 메모·보완 요청 (선택)</summary><TextField fullWidth multiline minRows={2} label="검토 메모" value={note} disabled={busy} onChange={e=>setNote(e.target.value)} sx={{mt:1}}/><Button disabled={blocked||!note.trim()} onClick={()=>void review('reject')}>보완 요청 보내기</Button></details>
  <div className="collection-actions"><Button variant="contained" startIcon={<Check size={16}/>} disabled={blocked||dirty||incoming.issues.length>0||incoming.followups.length>0||incoming.question.dataset_changed||incoming.question.status!=='review'||!!incoming.conflicts.length&&!target} onClick={()=>void review('approve')}>이 내용으로 승인</Button></div>
  <p className="knowledge-help">보고 내용과 용어 해석을 검토해 승인합니다. 아직 모르는 데이터 연결은 미확인으로 남습니다.{incoming.followups.length>0?' 위 질문에 답하거나 모른다고 알려 주세요.':''}</p>
 </article>;
 return <article className="collection-detail"><QuestionContext question={d.question} projects={projects} status="review" kind={kindNames[proposal.kind]}/><h3>답변 원문과 지식 초안</h3>
  <details open><summary>질문과 답변 원문</summary><p><strong><QuestionText question={d.question}/></strong></p>{d.question.answers?.map(a=><blockquote key={a.id}><TargetBadges targets={a.targets} label="답변 대상"/><p>{a.text}</p><small>{a.user_name} · {date(a.created_at)}</small></blockquote>)}</details>
  <LocalDraftNotice disabled={busy} active={active} draft={local} onRestore={record=>dispatch({type:'restore',value:record.value,revision:Number(record.baseVersion)})}/>
  {unavailable?<Alert severity="warning" sx={{my:2}}>이 초안이 다른 사용자에 의해 처리되었거나 더 이상 조회되지 않습니다. 작성 중인 내용은 유지했으며 저장·승인은 중지했습니다.</Alert>:(changed||targetChanged)&&<Alert severity="warning" sx={{my:2}} action={<Button disabled={busy} onClick={()=>setCompare(true)}>내 내용과 최신 저장값 비교</Button>}>검토 초안 또는 반영 대상 지식이 변경되었습니다. 작성 중인 내용은 유지했습니다. 비교 후 다시 검토해 주세요.</Alert>}
  <DraftConflictDialog open={active&&compare} mine={draftReviewInput(state)} latest={{proposal:state.latest.proposal,review_note:state.latest.review_note,status:state.latest.status,target:selectedTarget?{content:selectedTarget.content,version:selectedTarget.version,status:selectedTarget.status}:null}} onClose={()=>setCompare(false)} onUseLatest={()=>{local.discard();dispatch({type:'reload'});setCompare(false)}} onKeepMine={!unavailable&&state.latest.status==='pending'&&!state.latest.question.dataset_changed&&state.latest.question.status!=='structuring'&&(!target||selectedTarget&&selectedTarget.kind===proposal.kind&&selectedTarget.scope===proposal.scope)?()=>{local.markEdited();dispatch({type:'rebase'});dispatch({type:'edit',value:{targetRevision:selectedTarget?.revision??null}});setCompare(false)}:undefined}/>
  <fieldset disabled={busy} style={{border:0,padding:0,margin:0,minWidth:0}}>
  <div className="collection-draft-fields"><TextField label="지식 이름" value={content.name} onChange={e=>change({name:e.target.value})}/><TextField label="업무 영역 (선택)" value={content.domain} onChange={e=>change({domain:e.target.value})}/>
   <TextField select label="적용 범위" value={proposal.scope} disabled={!['term','metric'].includes(proposal.kind)} onChange={e=>edit({type:'edit',value:{proposal:{...proposal,scope:e.target.value as 'common'|'database'},dirty:true}})}><MenuItem value="database">현재 DB의 공용 지식</MenuItem><MenuItem value="common">모든 DB의 공통 정의</MenuItem></TextField>
   <TextField multiline minRows={3} label="업무 설명" value={content.description} onChange={e=>change({description:e.target.value})}/>
   {proposal.kind==='metric'&&<TextField select label="지표 유형" value={content.details.metric_type??''} onChange={e=>change({details:{...content.details,metric_type:e.target.value||null}})}><MenuItem value="">미정</MenuItem>{[['ratio','비율'],['count','개수'],['sum','합계'],['other','기타']].map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>}
   {proposal.kind==='relation'&&<TextField select label="관계의 수" value={content.details.cardinality??''} onChange={e=>change({details:{...content.details,cardinality:e.target.value||null}})}><MenuItem value="">미정</MenuItem>{['one_to_one','many_to_one','one_to_many','many_to_many'].map(v=><MenuItem key={v} value={v}>{v}</MenuItem>)}</TextField>}
   {detailFields[proposal.kind].filter(([field])=>field!=='analysis_result'||!!content.details[field]).map(([field,label])=><div key={field}><TextField fullWidth multiline minRows={2} label={label} disabled={field==='analysis_result'} value={content.details[field]??''} onChange={e=>change({details:{...content.details,[field]:e.target.value}})}/>{d.evidence[`details.${field}`]&&<p className="collection-quote">답변 근거: “{d.evidence[`details.${field}`].quote}”</p>}</div>)}
   <TextField multiline minRows={2} label="아직 확인할 내용 (한 줄에 하나)" value={unresolved} onChange={e=>edit({type:'edit',value:{unresolved:e.target.value,proposal:{...proposal,content:{...content,unresolved:e.target.value.split('\n').map(v=>v.trim()).filter(Boolean)}},dirty:true}})}/>
  </div>
  <details><summary>LLM이 사용한 답변 근거</summary>{Object.entries(d.evidence).map(([f,e])=><p key={f}><strong>{f}</strong><br/>“{e.quote}”</p>)}<p>관리자가 수정한 필드는 아래 검토 사유와 함께 보관됩니다. 최초 초안과 원문은 유지됩니다.</p></details>
  {(d.issues.length>0||d.question.dataset_changed)&&<Alert severity="warning" sx={{my:2}}>{d.question.dataset_changed&&<p>데이터셋이 변경되어 승인할 수 없습니다.</p>}<ul>{d.issues.map(i=><li key={i}>{i}</li>)}</ul></Alert>}
  {!!d.followups.length&&<div className="collection-followups"><strong>후속 확인 질문</strong><ul>{d.followups.map((f,i)=><li key={i}>{f}</li>)}</ul></div>}
  {!!d.conflicts.length&&<Alert severity="info" sx={{my:2}}>같은 이름·별칭의 지식이 있습니다. 기존 지식 변경으로 선택하거나 지식 이름을 구분해 주세요.</Alert>}
  <TextField fullWidth select label="승인 반영 방식" value={target} onChange={e=>setTarget(e.target.value)}><MenuItem value="">새 지식으로 등록</MenuItem>{items.filter(i=>i.kind===proposal.kind&&i.scope===proposal.scope).map(i=><MenuItem key={i.id} value={i.id}>{i.content.name} · 새 버전으로 변경</MenuItem>)}</TextField>
  <TextField fullWidth multiline minRows={2} label="수정·검토 사유" value={note} onChange={e=>setNote(e.target.value)} sx={{mt:2}}/>
  <div className="collection-actions"><Button disabled={blocked||!dirty||!note.trim()} onClick={()=>void mutate(async()=>{const saved=await post<CollectedDraft>(`${base}/drafts/${d.id}`,{expected_revision:state.editRevision,proposal,note});local.clearSaved();dispatch({type:'saved',draft:saved})})}>초안 수정 저장</Button>
   <Button variant="contained" startIcon={<Check size={16}/>} disabled={blocked||dirty||!note.trim()||incoming.issues.length>0||incoming.question.dataset_changed||incoming.question.status==='structuring'} onClick={()=>void review('approve')}>승인하고 지식 반영</Button>
   <Button color="inherit" disabled={blocked||!note.trim()} onClick={()=>void review('reject')}>반려 · 다시 질문</Button></div>
  {dirty&&<p className="knowledge-help">변경 내용을 저장하고 검증 결과를 확인한 뒤 승인하세요.</p>}
  </fieldset>
 </article>;
}

function CollectionSettingsDialog({value,close,saved,active}:{active:boolean;value:CollectionSettings;close:()=>void;saved:()=>Promise<void>}){
 const [originalRevision]=useState(value.revision);
 const [enabled,setEnabled]=useState(value.enabled),[hours,setHours]=useState(value.interval_hours),[maximum,setMaximum]=useState(value.max_questions),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 async function save(){setBusy(true);setError(null);try{await post(`${base}/settings`,{expected_revision:originalRevision,enabled,interval_hours:hours,max_questions:maximum});await saved();close()}catch(e){setError(e)}finally{setBusy(false)}}
 return <Dialog open={active} onClose={busy?undefined:close} fullWidth maxWidth="sm"><DialogTitle>지식 수집 설정</DialogTitle><DialogContent className="collection-dialog"><ErrorNotice error={error}/>
  <FormControlLabel control={<Checkbox checked={enabled} disabled={!value.configured&&!enabled} onChange={e=>setEnabled(e.target.checked)}/>} label="자동 질문 생성 사용"/>
  <TextField label="실행 간격 (시간)" type="number" value={hours} onChange={e=>setHours(Number(e.target.value))} inputProps={{min:1,max:720}}/><TextField label="한 번에 생성할 질문 수" type="number" value={maximum} onChange={e=>setMaximum(Number(e.target.value))} inputProps={{min:1,max:10}}/>
  <p className="knowledge-help">현재 DB에 대해 저장합니다. 프로젝트 선택과 무관하게 실행하며, 많은 데이터셋은 여러 실행에 나누어 확인합니다. 변경이 없는 데이터셋은 건너뜁니다. 서버가 실행 중일 때만 자동 수집합니다. 원천 데이터 행은 전송하지 않습니다.</p>
 </DialogContent><DialogActions><Button disabled={busy} onClick={close}>닫기</Button><Button variant="contained" disabled={busy||hours<1||hours>720||maximum<1||maximum>10} onClick={()=>void save()}>설정 저장</Button></DialogActions></Dialog>;
}

function NewQuestionDialog({config,close,saved,active}:{active:boolean;config:CollectionSettings;close:()=>void;saved:(id:string)=>Promise<void>}){
 const [datasetId,setDatasetId]=useState(''),[busy,setBusy]=useState(false);
 const dataset=config.datasets.find(d=>d.id===datasetId);
 return <Dialog open={active} onClose={busy?undefined:close} fullWidth maxWidth="sm"><DialogTitle>업무 질문 작성</DialogTitle><DialogContent className="collection-dialog">
  <TextField select label="관련 데이터셋" disabled={busy} value={datasetId} onChange={e=>setDatasetId(e.target.value)}>{config.datasets.map(d=><MenuItem key={d.id} value={d.id}><DatasetLabel dataset={d}/></MenuItem>)}</TextField>
  {dataset?<NewQuestionForm key={dataset.id} dataset={dataset} active={active} close={close} saved={saved} onBusy={setBusy}/>:<p>데이터셋을 선택하면 이 대상에 보관한 질문을 복구할 수 있습니다.</p>}
 </DialogContent>{!dataset&&<DialogActions><Button onClick={close}>닫기</Button></DialogActions>}</Dialog>;
}
function NewQuestionForm({dataset,active,close,saved,onBusy}:{dataset:CollectionDataset;active:boolean;close:()=>void;saved:(id:string)=>Promise<void>;onBusy:(busy:boolean)=>void}){
 const [value,setValue]=useState(blankQuestionInput),[baseVersion,setBaseVersion]=useState(dataset.version),[compare,setCompare]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 const {title,reason,kind,topic,subject,targetKind,columnKey,targetValue}=value;
 const draft=useLocalDraft({kind:'knowledge-new-question',targetId:dataset.id,projectId:dataset.project_id,baseVersion,value,validate:validNewQuestionInput});
 const columns=dataset.target_columns??[],column=columns.find(c=>JSON.stringify([c.source_id,c.column])===columnKey),changed=baseVersion!==dataset.version;
 function edit(patch:Partial<NewQuestionInput>){draft.markEdited();setValue(previous=>({...previous,...patch}))}
 const target={kind:targetKind,column:targetKind==='dataset'?'':column?.column??'',source_id:targetKind==='dataset'?'':column?.source_id??'',value:targetKind==='column_value'?targetValue:''};
 async function save(){if(draft.blocked||changed||busy)return;setBusy(true);onBusy(true);setError(null);try{const q=await post<KnowledgeQuestion>(`${base}/questions`,{dataset_id:dataset.id,title,reason,kind,topic,subject:subject||[target.column,target.value].filter(Boolean).join(':'),targets:[target]});draft.clearSaved();await saved(q.id);close()}catch(e){setError(e)}finally{setBusy(false);onBusy(false)}}
 return <><ErrorNotice error={error}/><LocalDraftNotice disabled={busy} active={active} draft={draft} onRestore={record=>{setValue(record.value);setBaseVersion(Number(record.baseVersion))}}/>
  {changed&&<Alert severity="warning" action={<Button onClick={()=>setCompare(true)}>최신 대상 확인</Button>}>질문을 작성한 뒤 데이터셋 버전이 바뀌었습니다. 대상 컬럼을 다시 확인하세요.</Alert>}
  <DraftConflictDialog open={active&&compare} title="질문과 최신 데이터셋 확인" mine={value} latest={{dataset:dataset.name,version:dataset.version,columns:columns.map(c=>c.column)}} onClose={()=>setCompare(false)} onUseLatest={()=>{draft.discard();setValue(blankQuestionInput());setBaseVersion(dataset.version);setCompare(false)}} onKeepMine={()=>{draft.markEdited();setBaseVersion(dataset.version);setCompare(false)}}/>
  <TextField disabled={busy} select label="질문 대상 유형" value={targetKind} onChange={e=>edit({targetKind:e.target.value as NewQuestionInput['targetKind']})}><MenuItem value="dataset">데이터셋 전체</MenuItem><MenuItem value="column">컬럼 자체</MenuItem><MenuItem value="column_value">컬럼 안의 특정 값</MenuItem></TextField>
  {targetKind!=='dataset'&&<TextField disabled={busy} select label="대상 컬럼" value={columnKey} onChange={e=>edit({columnKey:e.target.value})}>{columns.map(c=><MenuItem key={JSON.stringify([c.source_id,c.column])} value={JSON.stringify([c.source_id,c.column])}>{c.column}{c.source_id?` · 원천: ${c.source_name}`:' · 데이터셋 컬럼'}</MenuItem>)}</TextField>}
  {targetKind==='column_value'&&<TextField disabled={busy} label="질문할 특정 값" value={targetValue} onChange={e=>edit({targetValue:e.target.value})} helperText="실제로 확인한 값을 입력하세요." inputProps={{maxLength:150}}/>}
  <TextField disabled={busy} label="질문" multiline minRows={2} value={title} onChange={e=>edit({title:e.target.value})} inputProps={{maxLength:1000}}/><TextField disabled={busy} label="질문이 필요한 이유" multiline minRows={2} value={reason} onChange={e=>edit({reason:e.target.value})} inputProps={{maxLength:2000}}/><TextField disabled={busy} select label="지식 종류" value={kind} onChange={e=>edit({kind:e.target.value as NewQuestionInput['kind']})}>{Object.entries(kindNames).filter(([v])=>v!=='evaluation').map(([v,l])=><MenuItem value={v} key={v}>{l}</MenuItem>)}</TextField><TextField disabled={busy} select label="질문 주제" value={topic} onChange={e=>edit({topic:e.target.value})}>{Object.entries(topics).map(([v,l])=><MenuItem value={v} key={v}>{l}</MenuItem>)}</TextField><TextField disabled={busy} label="세부 질문 주제 (선택)" value={subject} onChange={e=>edit({subject:e.target.value})} helperText="대상이 같아도 다른 내용을 묻는 경우 구분해 주세요." inputProps={{maxLength:200}}/>
  <DialogActions><Button disabled={busy} onClick={close}>닫기</Button><Button variant="contained" disabled={busy||draft.blocked||changed||!title.trim()||!reason.trim()||(targetKind!=='dataset'&&!column)||(targetKind==='column_value'&&!targetValue.trim())} onClick={()=>void save()}>질문 등록</Button></DialogActions>
 </>;
}
