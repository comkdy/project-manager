import {useState} from 'react';
import {Alert,Button,Chip} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,post} from './api';
import {EvaluationResult} from './EvaluationAnalysisPanel';
import {evaluationScopeFilters} from './evaluationAnalysis';
import type {EvaluationAnalysisReview} from './evaluationAnalysis';
import {dataAnalysisPath,dataAnalysisPending,dataAnalysisReviewable} from './chatDataAnalysis';
import type {ChatDataAnalysis} from './chatDataAnalysis';
import type {AuthSession} from './types';

export default function ChatDataAnalysisAnswer({value,project,workspace,active,editable,onOpen,onQuestion}:{value:ChatDataAnalysis;project:number|null;workspace:string;active:boolean;editable:boolean;onOpen:(id:string)=>void;onQuestion:(id:string)=>void}){
 const client=useQueryClient(),actor=client.getQueryData<AuthSession>(['auth-session'])?.user;
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const key=['chat-data-analysis',workspace,actor?.id,actor?.role,project,value.run_id];
 const query=useQuery({queryKey:key,queryFn:({signal})=>api<ChatDataAnalysis>(dataAnalysisPath(value.run_id!,project),{signal}),
  enabled:active&&!!value.run_id,retry:false,staleTime:0,
  refetchInterval:q=>active&&dataAnalysisPending(q.state.data??value)?1500:false});
 // A failed revalidation must never reveal the initial stale result.
 const current=query.isError?null:query.data??value;
 async function review(){
  if(!current?.run_id||busy||!editable)return;
  if(current.review?.question_id){onQuestion(current.review.question_id);return}
  setBusy(true);setError('');
  try{const result=await post<EvaluationAnalysisReview>(`/evaluation-analysis/${current.run_id}/submit-review`,{
   expected_revision:current.revision,workspace_token:workspace,note:''});
   client.setQueryData(key,{...current,review:result});await client.invalidateQueries({queryKey:['semantic']});onQuestion(result.question_id);
  }catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 if(!current)return <Alert severity="warning">{query.error?.message??'현재 범위에서 계산 결과를 확인할 수 없습니다.'}</Alert>;
 return <section className="chat-data-analysis" aria-label="챗에서 실행한 현재 데이터 분석">
  <p style={{whiteSpace:'pre-wrap'}}>{current.explanation}</p>
  {current.dataset&&<small>{current.dataset.name} · v{current.dataset.version}</small>}
  {dataAnalysisPending(current)&&current.status!=='needs_input'&&<p role="status">현재 데이터로 계산 중입니다. 이 화면을 닫아도 작업은 계속됩니다.</p>}
  {current.result&&<><p><Chip size="small" label="현재 데이터 계산 결과"/> <Chip size="small" label={current.review?.status==='approved'?'지식 승인됨':'지식 미승인'}/></p>
   <small>조회 {new Date(current.result.captured_at).toLocaleString('ko-KR')} · {current.result.row_count.toLocaleString()}행</small>
   {!!current.next_checks?.length&&<><h4>다음에 확인하면 좋은 점</h4>{current.next_checks.map((line,i)=><p key={i}>{line}</p>)}</>}
   <details><summary>계산 결과와 근거 확인</summary><EvaluationResult result={current.result}/>
    <p style={{whiteSpace:'pre-wrap'}}>{current.result.summary}</p>
    <p>적용 필터: {evaluationScopeFilters(current.scope?.filters??[]).join(' · ')||'추가 필터 없음'}</p>
    {current.plan?.columns?.map(c=><p key={`${c.role}:${c.column}`}><strong>{c.column}</strong> — {c.reason}<br/>{c.evidence.quote}</p>)}
    {current.plan?.row_basis&&<p>데이터 대응 근거: {current.plan.row_basis.quote}</p>}
    {current.plan?.limitations?.map((limit,i)=><p key={i}>{limit}</p>)}
   </details><p>{current.notice}</p></>}
  {current.questions?.filter(q=>q.status!=='answered').map(q=><div key={q.id}><p>{q.title}</p><Button onClick={()=>onQuestion(q.id)}>확인 요청에서 답변·검토</Button></div>)}
  {current.run_id&&<Button size="small" onClick={()=>onOpen(current.run_id!)}>{current.status==='needs_input'?'분석 이어가기':'분석 상세·작업 상태'}</Button>}
  {editable&&dataAnalysisReviewable(current)&&<Button size="small" variant="outlined" disabled={busy||query.isFetching} onClick={()=>void review()}>{current.review?'연결된 확인 요청 열기':'이 결과를 지식으로 검토 요청'}</Button>}
  {error&&<Alert severity="error">{error}</Alert>}
 </section>;
}
