import {useEffect,useRef,useState} from 'react';
import {Alert,Button} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,post} from './api';
import {ErrorNotice,Loading} from './components';
import AssistantProposalDialog from './AssistantProposalDialog';
import type {EvaluationAnalysisView} from './evaluationAnalysis';
import {recommendedDatasetPrepare,type DatasetRecommendation} from './evaluationDatasetState';

export default function EvaluationDatasetRecommendation({identity,workspace,active,onQuestion,onRun,onClose}:{identity:string;workspace:string;active:boolean;onQuestion:(id:string)=>void;onRun:(run:EvaluationAnalysisView)=>void;onClose:()=>void}){
 const client=useQueryClient(),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[review,setReview]=useState(false),started=useRef('');
 const key=['evaluation-dataset-recommendation',workspace,identity];
 const query=useQuery({queryKey:key,queryFn:({signal})=>api<DatasetRecommendation>(`/evaluation-datasets/${identity}`,{signal}),enabled:active,
  refetchInterval:q=>active&&['running','waiting'].includes(q.state.data?.status??'')?1500:false});
 const value=query.data;
 async function recommend(){
  if(!value||busy)return;setBusy(true);setError(null);
  try{const next=await post<DatasetRecommendation>(`/evaluation-datasets/${identity}/recommend`,{expected_revision:value.revision,workspace_token:workspace});client.setQueryData(key,next)}catch(e){setError(e)}finally{setBusy(false)}
 }
 useEffect(()=>{const token=`${identity}:${value?.revision}`;if(active&&!busy&&value?.status==='waiting'&&value.questions.length&&value.questions.every(q=>q.status==='answered')&&started.current!==token){started.current=token;void recommend()}},[active,busy,value,identity]);
 async function prepare(){
  if(!value||busy)return;setBusy(true);setError(null);
  try{const run=await post<EvaluationAnalysisView>('/evaluation-analysis/prepare',recommendedDatasetPrepare(value,workspace));await client.invalidateQueries({queryKey:['evaluation-dataset-recommendations']});onRun(run)}catch(e){setError(e)}finally{setBusy(false)}
 }
 async function applied(){setReview(false);await client.invalidateQueries({queryKey:['bootstrap']});await client.invalidateQueries({queryKey:['evaluation-datasets']});await client.invalidateQueries({queryKey:['evaluation-dataset-recommendations']});await query.refetch()}
 return <section aria-label="AI 데이터셋 추천" style={{marginTop:16}}>
  <Button onClick={onClose} disabled={busy}>지속 분석 목록으로</Button><h3>분석에 사용할 데이터</h3>
  <ErrorNotice error={error??query.error}/>
  {query.isPending||value?.status==='running'?<Loading label="승인 사례·지식과 데이터셋 구성을 확인하고 있습니다."/>:null}
  {value&&<><p>참고 사례: {value.case_name}</p>{value.summary&&<p>{value.summary}</p>}
   {value.status==='error'&&<Alert severity="warning">{value.message}</Alert>}
   {value.status==='error'&&<Button disabled={busy} onClick={()=>void recommend()}>다시 추천받기</Button>}
   {value.status==='waiting'&&<><p>데이터 연결을 먼저 확인합니다. 아래 질문에 자연어로 답변하고 승인하면 추천을 이어갑니다.</p>{value.questions.filter(q=>q.status!=='answered').map(q=><div key={q.id}><p>{q.title}</p><Button onClick={()=>onQuestion(q.id)}>확인 요청에서 답변·검토</Button></div>)}</>}
   {['ready','applied'].includes(value.status)&&<>
    {value.dataset&&<Alert severity="success">{value.status==='applied'?'작성한 데이터셋':'추천 데이터셋'}: {value.dataset.name} · v{value.dataset.version}</Alert>}
    <details style={{margin:'16px 0'}}><summary>추천 근거와 주의할 점</summary>{value.evidence?.map((e,i)=><p key={i}>{e.quote}</p>)}{value.alignment_basis&&<p>표본 대응 근거: {value.alignment_basis.quote}</p>}{value.limitations?.map((text,i)=><p key={i}>{text}</p>)}</details>
    {value.proposal&&<><p>적합한 기존 데이터셋이 없어 생성안을 준비했습니다. 기존 작성 화면에서 미리보고 수정한 뒤 적용할 수 있습니다.</p><Button variant="contained" onClick={()=>setReview(true)}>데이터셋 생성안 검토</Button></>}
    {value.dataset_id&&<Button variant="contained" disabled={busy} onClick={()=>void prepare()}>이 데이터셋으로 분석 조건 추천</Button>}
   </>}
   {review&&value.proposal&&<AssistantProposalDialog initial={value.proposal} context={{project_id:value.project_id}} datasets={[]} catalog={value.catalog??[]} onClose={()=>setReview(false)} onApplied={()=>void applied()}/>}
  </>}
 </section>;
}
