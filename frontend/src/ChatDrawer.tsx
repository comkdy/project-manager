import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Alert,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle,Drawer,IconButton,TextField} from '@mui/material';
import {ArrowLeft,ArrowUp,Database,History,Plus,SlidersHorizontal,Sparkles,Square,Trash2,X,Braces} from 'lucide-react';
import {api,post,streamChat} from './api';
import type {CardSpec,Proposal,QueryContext} from './types';
import {proposalDefinition,proposalTitle} from './chatProposal';
import {conversation,conversations} from './chatHistory';
import type {ChatMessage,ConversationContext,ConversationSummary} from './chatHistory';
import './chat-history.css';
import ChatAnalysisAnswer from './ChatAnalysisAnswer';
import ChatDataAnalysisAnswer from './ChatDataAnalysisAnswer';
import ChatKnowledgePanel from './ChatKnowledgePanel';
import ChatEvaluationCases from './ChatEvaluationCases';
import EvaluationAnalysisPanel from './EvaluationAnalysisPanel';
import type {EvaluationSuggestion} from './evaluationCases';
import {evaluationPreparation,messageSequence} from './evaluationExecution';
import {originalKnowledgeQuestion} from './chatKnowledgeState';
import type {AnalysisProposalView} from './analysisProposal.generated';
import type {KnowledgeDefinitionRef} from './chatKnowledge.generated';
import {loadAnalysisProposal} from './analysisProposalApi';

export default function ChatDrawer({open,onClose,configured,serverConfigured,onSettings,editable,context,projectName,datasetId,selectedCard,onProposal,appliedProposals=[],workspace,onOpenAnalysis,onOpenKnowledge,onOpenQuestion,onOpenRecords,onReviewAnalysis,evaluationEntry}:{open:boolean;onClose:()=>void;configured:boolean;serverConfigured:boolean;onSettings:()=>void;editable:boolean;context:QueryContext;projectName?:string;datasetId:string|null;selectedCard:CardSpec|null;onProposal:(p:Proposal)=>void;appliedProposals?:string[];workspace:string;onOpenAnalysis:(id:string)=>void;onOpenKnowledge:()=>void;onOpenQuestion:(id:string)=>void;onOpenRecords:()=>void;evaluationEntry?:{id:string;projectId:number|null;token:number}|null;onReviewAnalysis:(proposal:AnalysisProposalView)=>void}){
 const [messages,setMessages]=useState<ChatMessage[]>([]),[text,setText]=useState(''),[busy,setBusy]=useState(false),[status,setStatus]=useState(''),[error,setError]=useState('');
 const [saved,setSaved]=useState<ConversationSummary|null>(null),[resumeContext,setResumeContext]=useState<ConversationContext|null>(null);
 const [evaluationRun,setEvaluationRun]=useState<{id:string;projectId:number|null}|null>(null);
 useEffect(()=>{if(evaluationEntry)setEvaluationRun({id:evaluationEntry.id,projectId:evaluationEntry.projectId})},[evaluationEntry?.token]);
 const activeRevision=useRef<number|undefined>(undefined);
 const [showHistory,setShowHistory]=useState(false),[items,setItems]=useState<ConversationSummary[]>([]),[search,setSearch]=useState(''),[nextOffset,setNextOffset]=useState<number|null>(null),[nextBefore,setNextBefore]=useState<number|null>(null);
 const [loading,setLoading]=useState(false),[loadingList,setLoadingList]=useState(false),[remove,setRemove]=useState<ConversationSummary|null>(null),[remoteRunning,setRemoteRunning]=useState(false);
 const controller=useRef<AbortController|null>(null),bottom=useRef<HTMLDivElement>(null),activeId=useRef<string|null>(null),alive=useRef(true);
 const messagePane=useRef<HTMLDivElement>(null),prependPosition=useRef<{height:number;top:number}|null>(null);
 const project=context.project_id??null,locked=busy||loading||remoteRunning;
 const scope=project!==null?projectName??'현재 프로젝트':'전체 프로젝트';
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;controller.current?.abort()}},[]);
 useLayoutEffect(()=>{if(prependPosition.current&&messagePane.current){const old=prependPosition.current;messagePane.current.scrollTop=old.top+messagePane.current.scrollHeight-old.height;prependPosition.current=null}else if(!showHistory)bottom.current?.scrollIntoView({behavior:'smooth'})},[messages.length,status,showHistory]);
 useEffect(()=>{
  if(!open||!showHistory)return;
  const abort=new AbortController();setLoadingList(true);setError('');
  const timer=setTimeout(()=>{conversations(project,search,0,abort.signal).then(page=>{setItems(page.items);setNextOffset(page.next_offset)}).catch(e=>{if(e.name!=='AbortError')setError(e.message)}).finally(()=>{if(!abort.signal.aborted)setLoadingList(false)})},200);
  return()=>{clearTimeout(timer);abort.abort()};
 },[open,showHistory,project,search]);
 useEffect(()=>{
  if(!open||!saved||busy)return;
  let cancelled=false;
  const refresh=()=>conversation(saved.id).then(value=>{
   if(cancelled||activeId.current!==value.id)return;
   setSaved(value);setRemoteRunning(value.running);setMessages(value.messages.map(m=>({...m,restored:true})));setNextBefore(value.next_before);
  }).catch(e=>{if(!cancelled)setError(e.message)});
  void refresh();
  const timer=remoteRunning?setInterval(()=>void refresh(),2000):undefined;
  return()=>{cancelled=true;if(timer)clearInterval(timer)};
 },[open,saved?.id,busy,remoteRunning]);
 function newConversation(){
  if(busy||loading)return;
  activeId.current=null;setSaved(null);setResumeContext(null);setMessages([]);setNextBefore(null);setRemoteRunning(false);setText('');setStatus('');setError('');setShowHistory(false);
 }
 async function loadConversation(id:string){
  if(busy||loading)return;
  setLoading(true);setError('');
  try{const value=await conversation(id);if(!alive.current)return;
   activeId.current=id;setSaved(value);setResumeContext(value.context);setMessages(value.messages.map(m=>({...m,restored:true})));setNextBefore(value.next_before);setRemoteRunning(value.running);setText('');setStatus('');setShowHistory(false);
  }catch(e){setError((e as Error).message)}finally{setLoading(false)}
 }
 async function olderMessages(){
  if(!saved||!nextBefore)return;setLoading(true);
  try{const value=await conversation(saved.id,nextBefore);if(messagePane.current)prependPosition.current={height:messagePane.current.scrollHeight,top:messagePane.current.scrollTop};setMessages(m=>[...value.messages.map(v=>({...v,restored:true})),...m]);setNextBefore(value.next_before)}catch(e){setError((e as Error).message)}finally{setLoading(false)}
 }
 async function moreConversations(){
  if(nextOffset===null)return;setLoadingList(true);
  try{const page=await conversations(project,search,nextOffset);setItems(m=>[...m,...page.items]);setNextOffset(page.next_offset)}catch(e){setError((e as Error).message)}finally{setLoadingList(false)}
 }
 async function deleteSaved(){
  if(!remove)return;setLoading(true);setError('');
  try{await api(`/chat/conversations/${remove.id}`,{method:'DELETE'});setItems(m=>m.filter(v=>v.id!==remove.id));if(activeId.current===remove.id){activeId.current=null;setSaved(null);setMessages([]);setResumeContext(null);setNextBefore(null);setRemoteRunning(false)}setRemove(null)}catch(e){setError((e as Error).message)}finally{setLoading(false)}
 }
 async function review(m:ChatMessage){
  if(!m.proposal||locked||(m.knowledge??m.proposal.knowledge)?.availability==='unavailable'||(m.knowledge??m.proposal.knowledge)?.blocking)return;
  if(!m.restored){onProposal(m.proposal);return}
  setLoading(true);setError('');
  try{
   const result=await post<Proposal>('/chat/preview',{proposal:proposalDefinition(m.proposal),project_id:project,period_start:m.proposal.period_start??resumeContext?.period_start??context.period_start,workspace_token:workspace});
   onProposal({...result,proposal_id:m.proposal.proposal_id});
  }catch(e){setError((e as Error).message)}finally{setLoading(false)}
 }
 async function reviewAnalysis(value:AnalysisProposalView){
  if(locked)return;setLoading(true);setError('');
  try{const latest=await loadAnalysisProposal(value.proposal_id,value.project_id??null);if(!alive.current)return;
   if(latest.status==='confirmed'&&latest.run_id){onOpenAnalysis(latest.run_id);return}
   if(['unavailable','cancelled'].includes(latest.status))throw new Error(latest.explanation);
   onReviewAnalysis(latest);
  }catch(e){if(alive.current)setError((e as Error).message)}finally{if(alive.current)setLoading(false)}
 }
 async function openEvaluation(id:string){
  if(locked)return;setLoading(true);setError('');
  try{
   const latest=await api<{id:string;kind:string;project_id:number|null}>(`/analytics/runs/${encodeURIComponent(id)}`);
   if(!alive.current)return;
   if(latest.kind!=='evaluation')throw new Error('이 실행은 평가 사례 분석이 아닙니다. 이전 분석 결과에서 확인해 주세요.');
   setEvaluationRun({id:latest.id,projectId:latest.project_id});
  }catch(e){if(alive.current)setError((e as Error).message)}finally{if(alive.current)setLoading(false)}
 }
 async function prepareEvaluation(message:ChatMessage,suggestion:EvaluationSuggestion){
  if(!message.evaluation_cases||locked||!editable)return;
  setLoading(true);setError('');
  try{
   const selectedContext=resumeContext??{project_id:project,period_start:context.period_start,dataset_id:datasetId??selectedCard?.dataset_id,card:selectedCard};
   const body=evaluationPreparation(message.evaluation_cases,suggestion,selectedContext,activeId.current??undefined,messageSequence(message.id));
   const prepared=await post<{id:string;project_id:number|null}>('/evaluation-analysis/prepare',body);
   if(!alive.current)return;
   setEvaluationRun({id:prepared.id,projectId:prepared.project_id});
   if(activeId.current){const latest=await conversation(activeId.current);if(alive.current&&activeId.current===latest.id){setSaved(latest);setMessages(latest.messages.map(m=>({...m,restored:true})))}}
  }catch(e){if(alive.current)setError((e as Error).message)}finally{if(alive.current)setLoading(false)}
 }
 async function send(content=text,knowledgeSelection?:{knowledge_context_id:string;selected_knowledge:KnowledgeDefinitionRef[]}){
  if(!content.trim()||locked||!configured||!editable)return;
  const requestContext=resumeContext??{project_id:project,period_start:context.period_start,dataset_id:datasetId??selectedCard?.dataset_id??undefined,card:selectedCard??undefined};
  setMessages(m=>[...m,{role:'user',text:content}]);setText('');setBusy(true);setError('');setStatus('요청을 확인하고 있습니다…');
  controller.current=new AbortController();let received=false;
  try{
   await streamChat({...requestContext,message:content,...knowledgeSelection,save_history:true,conversation_id:saved?.id,conversation_revision:saved?.revision},(event,data)=>{
    if(!alive.current)return;
    if(event==='conversation'){activeId.current=data.id;activeRevision.current=data.revision;setSaved(data);setResumeContext(requestContext)}
    if(event==='status')setStatus(data.message);
    if(event==='proposal'){received=true;setMessages(m=>[...m,{role:'assistant',text:data.explanation,proposal:data,knowledge:data.knowledge}]);setStatus('')}
    if(event==='analysis_proposal'){received=true;setMessages(m=>[...m,{role:'assistant',text:data.explanation,analysis_proposal:data}]);setStatus('')}
    if(event==='analysis'){received=true;setMessages(m=>[...m,{role:'assistant',text:data.explanation,analysis:data}]);setStatus('')}
    if(event==='data_analysis'){received=true;setMessages(m=>[...m,{role:'assistant',text:data.explanation,data_analysis:data}]);setStatus('')}
    if(event==='evaluation_cases'){received=true;setMessages(m=>[...m,{id:activeRevision.current?`${activeRevision.current}-assistant`:undefined,role:'assistant',text:data.explanation,evaluation_cases:data}]);setStatus('')}
    if(event==='clarification'){received=true;setMessages(m=>[...m,{role:'assistant',text:data.question,options:data.options,knowledge:data.knowledge}]);setStatus('')}
    if(event==='error'){received=true;setError(data.message);setStatus('')}
   },controller.current.signal);
   if(!received)setStatus('응답이 종료되었습니다. 다시 시도하세요.');
  }catch(e){if((e as Error).name==='AbortError')setStatus('요청을 중지했습니다.');else setError((e as Error).message)}finally{if(alive.current)setBusy(false)}
 }
 const suggestions=['Lot별 수율을 조회하는 SQL을 작성해줘','장비별 측정값 데이터셋을 만들어줘','프로젝트별 수율을 막대그래프로 만들어줘','현재 데이터와 관련된 평가 사례를 찾아 추가 분석을 제안해줘'];
 return <><Drawer anchor="right" open={open} onClose={onClose} slotProps={{paper:{className:'chat-drawer'}}}>
  <div className="chat-header"><div className="ai-orb"><Sparkles size={21}/></div><div><h2>Project Manager AI</h2><span><i className={configured?'online-dot':'offline-dot'}/>{configured?'LLM 설정됨':'LLM 연결 대기'}</span></div><IconButton onClick={onClose} aria-label="어시스턴트 닫기"><X size={20}/></IconButton></div>
  <div className="chat-history-toolbar"><Button size="small" startIcon={showHistory?<ArrowLeft size={15}/>:<History size={15}/>} onClick={()=>setShowHistory(!showHistory)} disabled={busy||loading}>{showHistory?'대화로 돌아가기':'이전 대화'}</Button><Button size="small" startIcon={<Plus size={15}/>} onClick={newConversation} disabled={busy||loading}>새 대화</Button><Button size="small" onClick={onOpenRecords}>이전 분석 결과</Button></div>
  <div className="chat-context"><Database size={13}/><span>{scope}</span>{(resumeContext?resumeContext.card:selectedCard)&&<Chip size="small" label={(resumeContext?resumeContext.card:selectedCard)?.title}/>}</div>
  {showHistory?<div className="chat-history-list">
   <h3>이전 대화</h3><p className="chat-history-hint">현재 DB · {scope} · 내 대화</p>
   <TextField fullWidth size="small" label="대화 제목 검색" value={search} onChange={e=>setSearch(e.target.value)}/>
   {loadingList&&!items.length&&<p role="status">대화를 불러오고 있습니다…</p>}
   {!loadingList&&!items.length&&<p className="chat-history-empty">{search?'검색한 대화가 없습니다.':'저장된 대화가 없습니다. 첫 질문을 보내면 자동으로 저장됩니다.'}</p>}
   {items.map(item=><div className="chat-history-item" key={item.id}><button className="chat-history-open" onClick={()=>loadConversation(item.id)} disabled={busy||loading}><strong>{item.title}</strong><span>{new Date(item.updated_at).toLocaleString('ko-KR',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})} · 질문 {item.revision}개</span></button><IconButton size="small" aria-label={`${item.title} 대화 삭제`} onClick={()=>setRemove(item)} disabled={busy||loading}><Trash2 size={15}/></IconButton></div>)}
   {nextOffset!==null&&<Button fullWidth onClick={moreConversations} disabled={loadingList}>더 불러오기</Button>}
   {error&&<Alert severity="error">{error}</Alert>}
  </div>:<>
  {saved&&<div className="chat-conversation-title"><strong>{saved.title}</strong><span>자동 저장됨</span></div>}
  <div className="chat-messages" ref={messagePane}>
   {nextBefore!==null&&<Button fullWidth size="small" onClick={olderMessages} disabled={locked}>앞선 메시지 불러오기</Button>}
   {!messages.length&&<div className="chat-welcome"><h3>데이터 조회부터 분석까지</h3><p>추세·split 비교, SQL 작성, 데이터셋·카드 구성을 요청하세요.</p><div className="chat-suggestions">{suggestions.map((s,i)=><button key={s} onClick={()=>setText(s)}><span>{i===0?<Braces size={16}/>:i===1?<Database size={16}/>:<SlidersHorizontal size={16}/>}</span>{s}<Plus size={14}/></button>)}</div></div>}
   {!configured&&<Alert severity="info" action={<Button size="small" onClick={onSettings}>설정</Button>}>{serverConfigured?'프로필에서 본인의 LLM API 키를 등록해 주세요.':'관리자가 서버의 LLM endpoint와 모델을 설정해야 채팅을 사용할 수 있습니다.'}</Alert>}
   {!editable&&<Alert severity="info">변경 제안은 편집자 또는 관리자 계정에서 사용할 수 있습니다.</Alert>}
   {resumeContext&&<div className="chat-resume-context"><span>이 대화의 데이터셋·카드·기간 기준을 이어서 사용합니다.</span><button onClick={()=>setResumeContext({project_id:project,period_start:context.period_start,dataset_id:datasetId,card:selectedCard})} disabled={locked}>현재 화면 기준 사용</button></div>}
   {messages.map((m,i)=><div className={`chat-message ${m.role}`} key={m.id??i}>
    {m.role==='assistant'&&<div className="assistant-label"><Sparkles size={13}/>PROJECT MANAGER AI</div>}{m.data_analysis?<ChatDataAnalysisAnswer value={m.data_analysis} project={project} workspace={workspace} active={open&&!showHistory} editable={editable} onOpen={id=>void openEvaluation(id)} onQuestion={onOpenQuestion}/>:m.analysis?<ChatAnalysisAnswer analysis={m.analysis} text={m.text} workspace={workspace}/>:<p>{m.text}</p>}
    {m.options&&!(m.knowledge?.clarifications?.length)&&<div className="chat-clarification-options">{m.options.map(option=><Button key={option} size="small" variant="outlined" disabled={locked||!configured||!editable} onClick={()=>send(option)}>{option}</Button>)}</div>}
    {m.evaluation_cases&&<ChatEvaluationCases value={m.evaluation_cases} disabled={locked||!editable} onSuggest={setText} onExecute={suggestion=>void prepareEvaluation(m,suggestion)} onOpenAnalysis={id=>void openEvaluation(id)} onOpenKnowledge={onOpenKnowledge}/>}
    {(m.knowledge??m.proposal?.knowledge)&&<ChatKnowledgePanel key={(m.knowledge??m.proposal?.knowledge)!.context_id} knowledge={(m.knowledge??m.proposal?.knowledge)!} restored={m.restored} disabled={locked||!configured||!editable} onSelect={originalKnowledgeQuestion(messages,i)!==null?refs=>{const original=originalKnowledgeQuestion(messages,i)!;void send(original,{knowledge_context_id:(m.knowledge??m.proposal?.knowledge)!.context_id,selected_knowledge:refs})}:undefined}/>}
    {m.analysis_proposal&&<div className="analysis-proposal-message"><h4>{m.analysis_proposal.status==='confirmed'?'확인한 분석 조건':'실행 전 조건 확인'}</h4><p>기간·대상·처리 기준을 확인하고 필요한 값을 직접 수정합니다. 확인 버튼을 누르기 전에는 분석을 실행하지 않습니다.</p>{['unavailable','cancelled'].includes(m.analysis_proposal.status)?<Alert severity="warning">{m.analysis_proposal.explanation}</Alert>:<Button size="small" variant="outlined" disabled={locked||m.analysis_proposal.status==='pending'&&!editable} onClick={()=>void reviewAnalysis(m.analysis_proposal!)}>{m.analysis_proposal.status==='confirmed'?'분석 결과 열기':'조건 확인·수정'}</Button>}</div>}
    {m.analysis&&<div className="chat-analysis-actions">
     {m.analysis.source_watermark?.captured_at&&<small>데이터 조회 시각: {new Date(m.analysis.source_watermark.captured_at).toLocaleString('ko-KR')}</small>}
     {m.analysis.run_id&&<Button size="small" variant="outlined" onClick={()=>onOpenAnalysis(m.analysis!.run_id!)}>분석 결과 열기</Button>}
     {!!m.analysis.questions?.length&&<Button size="small" onClick={onOpenKnowledge}>AI 지식 확인 요청 열기</Button>}
    </div>}
    {m.proposal&&<div className="proposal-card"><div className="proposal-type">{m.proposal.kind==='card'?<SlidersHorizontal size={15}/>:m.proposal.kind==='query'?<Braces size={15}/>:<Database size={15}/>}<strong>{proposalTitle(m.proposal)}</strong></div>
     <h4>{m.proposal.card?.title??m.proposal.dataset?.name}</h4>
     {m.proposal.kind==='card'&&m.proposal.dataset&&<p>새 데이터셋: {m.proposal.dataset.name}</p>}
     {m.proposal.query_result&&<p>SQL 검증 완료 · {m.proposal.query_result.rows.length}행 확인</p>}
     <Button fullWidth variant="outlined" size="small" disabled={locked||!editable||(m.knowledge??m.proposal.knowledge)?.availability==='unavailable'||(m.knowledge??m.proposal.knowledge)?.blocking||m.applied||(!!m.proposal.proposal_id&&appliedProposals.includes(m.proposal.proposal_id))} onClick={()=>review(m)}>{m.applied||(m.proposal.proposal_id&&appliedProposals.includes(m.proposal.proposal_id))?'적용 완료':m.restored?'다시 조회하고 제안 열기':'미리보기 및 적용'}</Button>
    </div>}
   </div>)}
   {remoteRunning&&<div className="chat-status" role="status">이 대화의 응답을 기다리고 있습니다…</div>}
   {loading&&<div className="chat-status" role="status">불러오고 있습니다…</div>}
   {status&&<div className="chat-status" role="status">{busy&&<span className="pulse-dot"/>}{status}</div>}{error&&<Alert severity="error">{error}</Alert>}<div ref={bottom}/>
  </div>
  <div className="chat-compose"><TextField fullWidth multiline minRows={2} maxRows={5} placeholder="예: Lot별 수율 카드를 만들어줘" value={text} onChange={e=>setText(e.target.value)} disabled={!configured||!editable||locked} slotProps={{htmlInput:{maxLength:6000}}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/><div className="chat-compose-footer"><span>{saved?'대화는 본인에게만 보입니다':'첫 질문부터 자동 저장됩니다'}</span>{busy?<IconButton onClick={()=>controller.current?.abort()} aria-label="생성 중지"><Square size={16}/></IconButton>:<IconButton color="primary" disabled={!text.trim()||!configured||!editable||locked} onClick={()=>send()} aria-label="메시지 보내기"><ArrowUp size={19}/></IconButton>}</div></div>
  </>}
  <Dialog open={!!remove} onClose={()=>!loading&&setRemove(null)}><DialogTitle>대화를 삭제할까요?</DialogTitle><DialogContent>선택한 대화와 메시지를 삭제합니다. 이미 적용한 데이터셋과 카드는 유지됩니다.{error&&<Alert severity="error">{error}</Alert>}</DialogContent><DialogActions><Button onClick={()=>setRemove(null)} disabled={loading}>취소</Button><Button color="error" onClick={deleteSaved} disabled={loading}>삭제</Button></DialogActions></Dialog>
 </Drawer><Drawer anchor="right" open={open&&!!evaluationRun} onClose={()=>setEvaluationRun(null)} slotProps={{paper:{sx:{width:'min(760px, 100vw)'}}}}>{evaluationRun&&<EvaluationAnalysisPanel key={evaluationRun.id} runId={evaluationRun.id} workspace={workspace} projectId={evaluationRun.projectId} active={open} onClose={()=>setEvaluationRun(null)} onOpenQuestion={onOpenQuestion} onOpenRun={id=>void openEvaluation(id)} canRun={editable}/>}</Drawer></>;
}
