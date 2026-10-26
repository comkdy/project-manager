import {useEffect,useState} from 'react';
import {Alert,Button,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api,post} from './api';
import {ErrorNotice} from './components';
import KnowledgeWatchControls from './KnowledgeWatchControls';
import EvaluationDatasetRecommendation from './EvaluationDatasetRecommendation';
import {preferredSetupEntry,type DatasetRecommendation} from './evaluationDatasetState';
import EvaluationAnalysisPanel from './EvaluationAnalysisPanel';
import type {EvaluationAnalysisView} from './evaluationAnalysis';
import type {KnowledgeItem} from './semanticTypes';
import type {AuthSession,Project} from './types';

type Dataset={id:string;name:string;version:number;project_id:number|null};
export default function KnowledgeWatchWorkspace({workspace,items,projects,active,onQuestion,requestedRun,requestedDataset}:{workspace:string;items:KnowledgeItem[];projects:Project[];active:boolean;onQuestion:(id:string)=>void;requestedDataset?:{id:string;entry:number}|null;requestedRun?:{id:string;project_id:number|null;entry:number}|null}){
 const client=useQueryClient(),user=client.getQueryData<AuthSession>(['auth-session'])?.user;
 const [selected,setSelected]=useState(''),[datasetId,setDatasetId]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 const [discovery,setDiscovery]=useState<string|null>(null),[scopeProject,setScopeProject]=useState<number|null>(null);
 const [run,setRun]=useState<{id:string;project_id:number|null}|null>(null),[preparing,setPreparing]=useState(false);
 useEffect(()=>{const entry=preferredSetupEntry(requestedRun,requestedDataset);if(entry?.kind==='dataset'){setDiscovery(entry.value.id);setRun(null)}else if(entry){setRun(entry.value);setDiscovery(null)}},[requestedRun,requestedDataset]);
 const canRun=!!user&&user.role!=='viewer';
 const discoveries=useQuery({queryKey:['evaluation-dataset-recommendations',workspace,user?.id,user?.role],queryFn:({signal})=>api<DatasetRecommendation[]>('/evaluation-datasets',{signal}),enabled:active&&canRun&&!run&&!discovery,refetchInterval:active&&!run&&!discovery?15000:false});
 const pending=useQuery({queryKey:['evaluation-setups',workspace,user?.id,user?.role],queryFn:({signal})=>api<EvaluationAnalysisView[]>('/evaluation-analysis?setup_only=true',{signal}),enabled:active&&canRun&&!run,refetchInterval:active&&!run?15000:false});
 const datasets=useQuery({queryKey:['evaluation-datasets',workspace],queryFn:({signal})=>api<Dataset[]>('/semantic/collection/evaluation-datasets',{signal}),enabled:active&&preparing&&canRun});
 const cases=items.filter(i=>i.kind==='evaluation'&&i.published_version);
 const chosen=cases.find(c=>c.id===selected),owner=chosen?.evaluation_context?.case_context?.project_id??chosen?.evaluation_context?.dataset?.project_id??null;
 const analysisProject=owner??scopeProject;
 const choices=(datasets.data??[]).filter(d=>d.project_id===null||d.project_id===analysisProject);
 async function prepare(){
  const dataset=choices.find(d=>d.id===datasetId);if(!chosen||!dataset)return;
  setBusy(true);setError(null);
  try{const next=await post<EvaluationAnalysisView>('/evaluation-analysis/prepare',{explicit_case:true,case_ref:{id:chosen.id,version:chosen.published_version},suggestion_id:`${chosen.id}:recheck`,search:{query:chosen.content.name,dataset_id:dataset.id,project_id:analysisProject??dataset.project_id}});setRun(next);setPreparing(false)}catch(e){setError(e)}finally{setBusy(false)}
 }
 async function discover(){
  if(!chosen||busy)return;setBusy(true);setError(null);
  try{const next=await post<DatasetRecommendation>('/evaluation-datasets',{case_ref:{id:chosen.id,version:chosen.published_version},project_id:analysisProject,workspace_token:workspace});setDiscovery(next.id);setPreparing(false)}catch(e){setError(e)}finally{setBusy(false)}
 }
 if(!canRun)return null;
 if(run)return <EvaluationAnalysisPanel key={run.id} runId={run.id} workspace={workspace} projectId={run.project_id} active={active} canRun onClose={()=>setRun(null)} onOpenQuestion={onQuestion} onOpenRun={id=>setRun({...run,id})} returnLabel="지속 분석 목록으로"/>;
 if(discovery)return <EvaluationDatasetRecommendation key={discovery} identity={discovery} workspace={workspace} active={active} onQuestion={onQuestion} onRun={next=>{setRun(next);setDiscovery(null)}} onClose={()=>setDiscovery(null)}/>;
 return <section style={{margin:'20px 0',padding:'16px',border:'1px solid #dce5e5',borderRadius:10}}>
  <KnowledgeWatchControls workspace={workspace} active={active} onOpenQuestion={onQuestion} onOpenRun={(id,project_id)=>setRun({id,project_id})}/>
  {!!pending.data?.length&&<section aria-label="이어갈 분석 추천"><h3>확인 중인 분석 추천</h3>{pending.data.map(r=><p key={r.id}>{r.case_ref?.name??r.question??'챗 데이터 분석'} · {r.dataset.name} <Button onClick={()=>setRun(r)}>{r.recommendation?.status==='waiting'?'연결 확인 이어가기':'추천 조건 보기'}</Button></p>)}</section>}
  {!!discoveries.data?.length&&<section aria-label="이어갈 데이터셋 추천"><h3>확인 중인 데이터셋 추천</h3>{discoveries.data.map(r=><p key={r.id}>{r.case_name} <Button onClick={()=>setDiscovery(r.id)}>데이터 추천 이어가기</Button></p>)}</section>}
  <Button onClick={()=>setPreparing(v=>!v)}>{preparing?'준비 닫기':'승인 사례로 분석 준비'}</Button>
  {preparing&&<div style={{display:'grid',gap:16,marginTop:16}}>
   <p>사례를 선택하면 AI가 분석에 맞는 데이터셋을 먼저 찾습니다. 적절한 데이터셋이 없으면 기존 작성 기능에서 검토할 생성안을 제안합니다. 연결을 확인한 뒤 분석 조건을 추천합니다.</p>
   {!cases.length&&<Alert severity="info">먼저 평가 사례를 등록하고 확인 요청에서 승인하세요.</Alert>}
   <TextField select label="참고할 승인 사례" value={selected} disabled={busy} onChange={e=>{setSelected(e.target.value);setDatasetId('');setScopeProject(null)}}><MenuItem value="">선택하세요</MenuItem>{cases.map(c=><MenuItem key={c.id} value={c.id}>{c.content.name}</MenuItem>)}</TextField>
   {chosen&&<p>{chosen.content.description}</p>}
   {chosen&&owner===null&&<TextField select label="분석할 범위" value={scopeProject??'common'} disabled={busy} onChange={e=>{setScopeProject(e.target.value==='common'?null:Number(e.target.value));setDatasetId('')}}><MenuItem value="common">공통 데이터</MenuItem>{projects.map(p=><MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}</TextField>}
   <ErrorNotice error={error??discoveries.error}/><Button variant="contained" disabled={busy||!chosen} onClick={()=>void discover()}>AI에게 데이터셋 추천받기</Button>
   <details><summary>데이터셋을 직접 선택하기</summary><TextField select fullWidth label="분석할 데이터셋" value={datasetId} disabled={busy||!chosen} onChange={e=>setDatasetId(e.target.value)} helperText="공정 이력·Inline·EPM이 같은 표본으로 대응된 비교용 데이터셋을 선택하세요. 기존 채팅으로 구성할 수도 있습니다."><MenuItem value="">선택하세요</MenuItem>{choices.map(d=><MenuItem key={d.id} value={d.id}>{d.name} · {d.project_id===null?'공통':projects.find(p=>p.id===d.project_id)?.name??'프로젝트'}</MenuItem>)}</TextField>
   <ErrorNotice error={error??datasets.error}/><Button variant="contained" disabled={busy||!chosen||!choices.some(d=>d.id===datasetId)} onClick={()=>void prepare()}>AI에게 분석 조건 추천받기</Button></details>
  </div>}
 </section>;
}
