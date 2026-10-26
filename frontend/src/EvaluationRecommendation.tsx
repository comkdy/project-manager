import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Checkbox,FormControlLabel,MenuItem,TextField} from '@mui/material';
import {post} from './api';
import {ErrorNotice,Loading} from './components';
import type {EvaluationAnalysisView} from './evaluationAnalysis';
import {evaluationRecommendationBody} from './evaluationAnalysis';

const roles:Record<string,string>={target:'확인할 결과',feature:'함께 볼 항목',group:'비교 구분',unit:'같은 표본 구분',time:'결과 측정 시점'};
const methods:Record<string,string>={group_compare:'그룹별 차이 비교',correlation:'두 항목의 관계 확인',ml_regression:'통계와 ML로 여러 요인 비교'};
export default function EvaluationRecommendation({run,workspace,active,disabled,onUpdate,onQuestion}:{run:EvaluationAnalysisView;workspace:string;active:boolean;disabled:boolean;onUpdate:(run:EvaluationAnalysisView)=>void;onQuestion:(id:string)=>void}){
 const [busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null),[continuous,setContinuous]=useState(true),[minutes,setMinutes]=useState(60);
 const started=useRef(''),mounted=useRef(true),plan=run.recommendation;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 async function recommend(){
  if(busy||disabled)return;
  setBusy(true);setError(null);
  try{const next=await post<EvaluationAnalysisView>(`/evaluation-analysis/${run.id}/recommend`,{expected_revision:run.revision,workspace_token:workspace});if(mounted.current)onUpdate(next)}catch(e){if(mounted.current)setError(e)}finally{if(mounted.current)setBusy(false)}
 }
 useEffect(()=>{
  const readyToRefresh=plan?.status==='waiting'&&plan.questions.length>0&&plan.questions.every(q=>q.status==='answered');
  const key=`${run.id}:${run.revision}`;
  if(active&&!disabled&&!busy&&(!plan||readyToRefresh)&&started.current!==key){started.current=key;void recommend()}
 },[active,disabled,busy,run.id,run.revision,plan]);
 async function confirm(){
  if(busy||disabled||plan?.status!=='ready'||!plan.settings)return;
  setBusy(true);setError(null);
  try{const next=await post<EvaluationAnalysisView>(`/evaluation-analysis/${run.id}/execute`,evaluationRecommendationBody(run,workspace,continuous,minutes));if(mounted.current)onUpdate(next)}catch(e){if(mounted.current)setError(e)}finally{if(mounted.current)setBusy(false)}
 }
 return <section className="evaluation-analysis-review" aria-label="AI 추천 분석 조건"><h3>AI가 추천하는 첫 분석</h3>
  <ErrorNotice error={error}/>
  {busy||plan?.status==='running'?<Loading label="승인된 사례와 데이터의 의미·연결을 확인하고 있습니다."/>:null}
  {plan?.summary&&<p>{plan.summary}</p>}
  {plan?.status==='error'&&<Alert severity="warning">{plan.message}</Alert>}
  {(!plan||plan.status==='error')&&<Button disabled={busy||disabled} onClick={()=>void recommend()}>AI에게 분석 조건 추천받기</Button>}
  {plan?.status==='waiting'&&<><p>아래 연결을 먼저 확인하면 추천을 이어갑니다. 자연어로 설명하고 승인한 내용이 분석 기준에 반영됩니다.</p>{plan.questions.filter(q=>q.status!=='answered').map(q=><div key={q.id}><p>{q.title}</p><Button disabled={busy} onClick={()=>onQuestion(q.id)}>확인 요청에서 답변·검토</Button></div>)}</>}
  {plan?.status==='ready'&&plan.settings&&<>
   <strong>{methods[plan.settings.method]}</strong>
   <dl>{plan.columns?.map(c=><div key={`${c.role}:${c.column}`}><dt>{roles[c.role]} · <b>{c.column}</b></dt><dd>{c.reason}</dd></div>)}</dl>
   <details><summary>추천 근거와 적용 범위</summary>{plan.columns?.map(c=><p key={`${c.role}:${c.column}`}><strong>{c.column}</strong> — {c.evidence.quote}</p>)}{plan.row_basis&&<p>데이터 대응 근거: {plan.row_basis.quote}</p>}{plan.limitations?.map((line,i)=><p key={i}>{line}</p>)}<p>저장된 데이터셋과 화면에 표시된 필터를 사용합니다. 과거 사례의 조건을 임의로 추가하지 않습니다.</p></details>
   <FormControlLabel control={<Checkbox checked={continuous} disabled={busy||disabled} onChange={e=>setContinuous(e.target.checked)}/>} label="이후 데이터가 바뀌면 같은 조건으로 지속 분석"/>
   {continuous&&<TextField select size="small" label="변경 확인 주기" value={minutes} disabled={busy||disabled} onChange={e=>setMinutes(Number(e.target.value))}>{[[15,'15분'],[60,'1시간'],[360,'6시간'],[1440,'하루']].map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField>}
   <p>표시된 연결과 비교 목적이 맞는지만 확인하세요. 계산 결과는 검토·승인한 뒤 AI 지식에 반영됩니다.</p>
   <Button variant="contained" disabled={busy||disabled} onClick={()=>void confirm()}>이 추천으로 분석 시작</Button>
  </>}
 </section>;
}
