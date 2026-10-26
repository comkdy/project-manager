import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Chip,MenuItem,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ArrowLeft,BookOpen,Play,RefreshCw} from 'lucide-react';
import {api,post} from './api';
import {ErrorNotice,Loading} from './components';
import JobActionButtons from './JobActionButtons';
import KnowledgeWatchControls from './KnowledgeWatchControls';
import EvaluationRecommendation from './EvaluationRecommendation';
import type {AuthSession} from './types';
import {blankEvaluationAnalysis,evaluationAnalysisBusy,evaluationAnalysisCurrent,evaluationAnalysisExecuteBody,evaluationAnalysisInput,evaluationAnalysisNumber as number,evaluationAnalysisReviewBody,evaluationScopeFilters} from './evaluationAnalysis';
import type {EvaluationAnalysisInput,EvaluationAnalysisResult,EvaluationAnalysisReview,EvaluationAnalysisView} from './evaluationAnalysis';
import './evaluation-analysis.css';

const statusNames:Record<string,string>={needs_input:'분석 조건 확인',queued:'대기 중',running:'현재 데이터 분석 중',completed:'분석 완료',failed:'분석 실패',cancelled:'취소됨',cancelling:'취소 요청됨'};
const date=(value:string)=>new Date(value).toLocaleString('ko-KR');
type Props={runId:string;workspace:string;projectId?:number|null;active?:boolean;onClose:()=>void;onOpenQuestion:(questionId:string)=>void;onOpenRun?:(id:string)=>void;canRun:boolean;returnLabel?:string};

export default function EvaluationAnalysisPanel({runId,workspace,projectId=null,active=true,onClose,onOpenQuestion,onOpenRun,canRun,returnLabel='채팅으로'}:Props){
 const client=useQueryClient(),actor=client.getQueryData<AuthSession>(['auth-session'])?.user;
 const queryKey=['evaluation-analysis',workspace,actor?.id,actor?.role,projectId,runId];
 const [input,setInput]=useState<EvaluationAnalysisInput>(blankEvaluationAnalysis),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 const initialized=useRef<string|null>(null),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 const query=useQuery({queryKey,queryFn:({signal})=>api<EvaluationAnalysisView>(`/evaluation-analysis/${encodeURIComponent(runId)}${projectId===null?'':`?project_id=${projectId}`}`,{signal}),enabled:active,retry:false,staleTime:0,refetchOnMount:'always',refetchInterval:q=>active&&(evaluationAnalysisBusy(q.state.data)||q.state.data?.recommendation?.status==='running'||q.state.data?.recommendation?.status==='waiting')?1500:false});
 const run=!query.isError&&evaluationAnalysisCurrent(query.data,runId,workspace,projectId)?query.data:undefined;
 useEffect(()=>{if(run&&initialized.current!==run.id){initialized.current=run.id;setInput(evaluationAnalysisInput(run.settings));setNote('');setError(null)}},[run]);
 const locked=busy||query.isFetching||!canRun||run?.status!=='needs_input';
 function edit(patch:Partial<EvaluationAnalysisInput>){setInput(value=>({...value,...patch}));setError(null)}
 async function execute(){
  if(!run||locked)return;
  setError(null);
  try{
   const body=evaluationAnalysisExecuteBody(run,input,workspace);setBusy(true);
   const next=await post<EvaluationAnalysisView>(`/evaluation-analysis/${encodeURIComponent(run.id)}/execute`,body);
   if(mounted.current)client.setQueryData(queryKey,next);
  }catch(cause){if(mounted.current){setError(cause);void query.refetch()}}finally{if(mounted.current)setBusy(false)}
 }
 async function submitReview(){
  if(!run||busy||query.isFetching||!canRun)return;
  if(run.review?.question_id){onOpenQuestion(run.review.question_id);return}
  setError(null);setBusy(true);
  try{
   const review=await post<EvaluationAnalysisReview>(`/evaluation-analysis/${encodeURIComponent(run.id)}/submit-review`,evaluationAnalysisReviewBody(run,workspace,note));
   if(mounted.current){client.setQueryData(queryKey,{...run,review});await client.invalidateQueries({queryKey:['semantic']});onOpenQuestion(review.question_id)}
  }catch(cause){if(mounted.current){setError(cause);void query.refetch()}}finally{if(mounted.current)setBusy(false)}
 }
 return <section className="evaluation-analysis-panel" aria-label="평가 사례 기반 현재 데이터 분석">
  <div className="evaluation-analysis-heading"><Button size="small" startIcon={<ArrowLeft size={16}/>} onClick={onClose}>{returnLabel}</Button><Button size="small" startIcon={<RefreshCw size={16}/>} disabled={busy||query.isFetching} onClick={()=>void query.refetch()}>상태 확인</Button></div>
  <h2>{run?.origin==='chat'?'질문에 대한 현재 데이터 분석':'사례를 현재 데이터에서 확인'}</h2><ErrorNotice error={error??query.error}/>
  {query.isPending?<Loading label="사례와 데이터 범위를 확인하는 중"/>:!run?<Alert severity="warning">현재 작업공간과 프로젝트에서 이 분석을 확인할 수 없습니다.</Alert>:<>
   <Chip size="small" label={statusNames[run.status]??run.status}/>
   <JobActionButtons key={`${workspace}:${run.id}`} target={{kind:'analysis_run',id:run.id,project_id:run.project_id}} controls={run.controls} workspace={workspace} onOpenRun={onOpenRun} onChanged={()=>void query.refetch()}/>
   <div className="evaluation-analysis-scope"><strong>{run.dataset.name} · v{run.dataset.version}</strong>{run.case_ref?<p>참고 사례: {run.case_ref.name} · v{run.case_ref.version}</p>:<p>{run.question}</p>}
    {run.knowledge_basis&&<details><summary>분석의 바탕이 된 승인 지식</summary>{Object.entries(run.knowledge_basis).filter(([,text])=>text).map(([key,text])=><p key={key} style={{whiteSpace:'pre-wrap'}}>{text}</p>)}<small>이 관측에서 관련된 항목을 선택합니다. 과거 조건을 현재 데이터에 자동 적용하지 않습니다.</small></details>}
    <p>{run.project_id===null?'공통 데이터 범위':`선택한 프로젝트 범위 (#${run.project_id})`}{run.scope.card_title?` · 카드: ${run.scope.card_title}`:''}</p>
    {run.scope.period?<p>적용 기간: {run.scope.period.start.year}년 {run.scope.period.start.week}주 월요일부터 · {run.scope.period.column} ({run.scope.period.timezone})</p>:run.scope.period_start?<p>선택한 조회 주차는 이 분석에 적용되지 않습니다. 데이터셋과 필터 조건의 범위를 사용합니다.</p>:null}
    <details><summary>적용된 데이터 범위</summary><p>이 데이터셋의 저장된 조회 조건에 현재 선택한 범위를 함께 적용합니다.</p>{run.scope.filters.length?<ul>{evaluationScopeFilters(run.scope.filters).map((text,index)=><li key={index}>{text}</li>)}</ul>:<p>추가로 선택한 필터 없음</p>}</details>
   </div>
   {!!run.warnings?.length&&<details className="evaluation-analysis-method"><summary>분석 방법과 해석 범위</summary><ul>{run.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul></details>}
   {!canRun&&<Alert severity="info">분석 결과를 조회할 수 있습니다. 현재 데이터 실행은 편집 권한이 있는 사용자가 진행합니다.</Alert>}
   {run.status==='needs_input'&&canRun&&<EvaluationRecommendation run={run} workspace={workspace} active={active} disabled={locked} onUpdate={next=>client.setQueryData(queryKey,next)} onQuestion={onOpenQuestion}/>}
   {run.status==='needs_input'&&!run.recommendation&&<details><summary>직접 조건 선택 (선택)</summary><form className="evaluation-analysis-form" onSubmit={event=>{event.preventDefault();void execute()}}>
    <TextField select fullWidth size="small" label="확인할 내용" value={input.method} disabled={locked} onChange={event=>edit({method:event.target.value as EvaluationAnalysisInput['method'],metrics:[]})}><MenuItem value="group_compare">Recipe·장비 등 그룹별 차이</MenuItem><MenuItem value="correlation">두 측정 항목이 함께 변하는 관계</MenuItem><MenuItem value="ml_regression">여러 요인과 결과의 관계 · 통계와 ML</MenuItem></TextField>
    {input.method==='group_compare'&&<TextField select fullWidth size="small" label="그룹을 나눌 항목" value={input.groupColumn} disabled={locked} onChange={event=>edit({groupColumn:event.target.value})}><MenuItem value="">선택해 주세요</MenuItem>{run.columns.map(column=><MenuItem key={column.name} value={column.name}>{column.name}</MenuItem>)}</TextField>}
    <TextField select fullWidth size="small" label={input.method==='ml_regression'?'설명하고 싶은 결과 (EPM·Inline)':'측정 항목'} value={input.metrics} disabled={locked} helperText={input.method==='ml_regression'?'예측할 숫자 항목 1개를 선택하세요.':input.method==='correlation'?'같은 행에 대응된 숫자 항목 2개를 선택하세요.':'평균과 분포를 확인할 숫자 항목 1~2개를 선택하세요.'} SelectProps={{multiple:true}} onChange={event=>edit({metrics:typeof event.target.value==='string'?event.target.value.split(','):event.target.value as string[]})}>{run.columns.filter(column=>column.numeric).map(column=><MenuItem key={column.name} value={column.name} disabled={!input.metrics.includes(column.name)&&input.metrics.length>=(input.method==='ml_regression'?1:2)}>{column.name}</MenuItem>)}</TextField>
    {input.method==='ml_regression'&&<>
     <TextField fullWidth select size="small" label="관계를 확인할 항목 (Recipe·장비·Inline 등)" value={input.features} SelectProps={{multiple:true}} disabled={locked} onChange={e=>edit({features:typeof e.target.value==='string'?e.target.value.split(','):e.target.value as string[]})} helperText="사례의 관련 항목 중 결과 측정 전에 알 수 있는 항목을 최대 8개 선택하세요.">{run.columns.filter(c=>!input.metrics.includes(c.name)).map(c=><MenuItem key={c.name} value={c.name} disabled={!input.features.includes(c.name)&&input.features.length>=8}>{c.name}</MenuItem>)}</TextField>
     <TextField fullWidth select size="small" label="같은 표본을 묶는 항목 (Lot·wafer)" value={input.unitColumns} SelectProps={{multiple:true}} disabled={locked} onChange={e=>edit({unitColumns:typeof e.target.value==='string'?e.target.value.split(','):e.target.value as string[]})} helperText="같은 Lot의 영향이 크면 Lot까지 묶으세요. 같은 표본은 학습과 검증에 나누어 넣지 않습니다.">{run.columns.map(c=><MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}</TextField>
     <TextField fullWidth select size="small" label="결과 측정 시점" value={input.timeColumn} disabled={locked} onChange={e=>edit({timeColumn:e.target.value})} helperText="과거로 학습하고 이후 시점에서 검증합니다."><MenuItem value="">선택하세요</MenuItem>{run.columns.filter(c=>/^(timestamp|date)/i.test(c.type)).map(c=><MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}</TextField>
     <p>같은 행의 공정·Inline·EPM이 같은 표본과 올바른 시간으로 연결된 데이터셋을 사용하세요. 항목마다 단위가 다른 세로형 데이터는 기존 채팅에서 비교용 데이터셋으로 먼저 구성할 수 있습니다.</p>
    </>}
    {input.method==='group_compare'&&<details><summary>두 그룹의 평균 차이도 계산 (선택)</summary><p>데이터에 저장된 그룹 값을 그대로 입력하세요. 입력하지 않으면 각 그룹의 요약만 확인합니다.</p><TextField fullWidth size="small" label="기준 그룹 값" value={input.baseline} disabled={locked} onChange={event=>edit({baseline:event.target.value})}/><TextField fullWidth size="small" label="비교 그룹 값" value={input.comparison} disabled={locked} onChange={event=>edit({comparison:event.target.value})}/></details>}
    <p className="evaluation-analysis-help">선택한 데이터셋을 직접 조회해 계산합니다. 평가 사례에 적힌 수치를 현재 결과로 사용하지 않습니다.</p>
    <Button type="submit" variant="contained" startIcon={<Play size={16}/>} disabled={locked}>현재 데이터로 분석 실행</Button>
   </form></details>}
   {evaluationAnalysisBusy(run)&&<Loading label="선택한 데이터 범위에서 계산하고 있습니다. 이 화면을 닫아도 작업은 계속됩니다."/>}
   {run.error&&<Alert severity="warning">{run.error}</Alert>}
   {run.result&&<EvaluationResult result={run.result}/>}
   {run.status==='completed'&&run.result&&<div className="evaluation-analysis-review">
    {run.result.knowledge_eligible===false&&<Alert severity="warning">유효한 분석 수치가 부족해 지식 검토를 요청할 수 없습니다. 채팅에서 다른 데이터 범위나 측정 항목으로 다시 확인해 주세요.</Alert>}
    {run.review?.question_id?<><Alert severity={run.review.status==='approved'?'success':'info'}>{run.review.status==='approved'?'검토를 거쳐 AI 지식에 반영했습니다.':'결과를 질문·승인 흐름으로 보냈습니다. 승인되면 AI 지식에 반영됩니다.'}</Alert><Button startIcon={<BookOpen size={16}/>} onClick={()=>onOpenQuestion(run.review!.question_id)}>연결된 질문·검토 열기</Button></>:canRun?<><TextField fullWidth multiline minRows={2} label="검토할 메모 (선택)" value={note} onChange={event=>setNote(event.target.value)} disabled={busy} inputProps={{maxLength:2000}}/><p>현재 결과와 참고 사례, 조회 조건을 함께 보존합니다. 관리자가 검토·승인한 결과만 이후 관련 사례 검색에 사용합니다.</p><Button variant="outlined" startIcon={<BookOpen size={16}/>} disabled={busy||query.isFetching||run.result.knowledge_eligible===false} onClick={()=>void submitReview()}>AI 지식 검토 요청</Button></>:null}
   </div>}
   {canRun&&run.status==='completed'&&<KnowledgeWatchControls workspace={workspace} run={run} active={active} onOpenQuestion={onOpenQuestion} onOpenRun={id=>onOpenRun?.(id)}/>}
  </>}
 </section>;
}

export function EvaluationResult({result}:{result:EvaluationAnalysisResult}){
 const label=(column:string)=>result.item_labels?.[column]??column;
 return <section className="evaluation-analysis-result" aria-label="현재 데이터 계산 결과"><h3>현재 데이터에서 확인한 결과</h3><small>조회 {date(result.captured_at)} · 조회 행 {number(result.row_count)}개</small>
  {!!result.groups?.length&&<div className="evaluation-analysis-table"><table><caption>그룹별 측정값 요약</caption><thead><tr><th>그룹</th><th>측정 항목</th><th>유효 / 결측</th><th>평균</th><th>표준편차</th><th>최소 ~ 최대</th></tr></thead><tbody>{result.groups.flatMap((group,groupIndex)=>group.metrics.map(metric=><tr key={`${groupIndex}:${metric.column}`}><th>{group.value??'(빈 값)'}<small>{number(group.row_count)}행</small></th><th>{label(metric.column)}</th><td>{number(metric.valid_count)} / {number(metric.missing_count)}</td><td>{number(metric.mean)}</td><td>{number(metric.stddev)}</td><td>{number(metric.min)} ~ {number(metric.max)}</td></tr>))}</tbody></table></div>}
  {!!result.deltas?.length&&<div className="evaluation-analysis-deltas">{result.deltas.map(delta=><p key={delta.column}><strong>{label(delta.column)}</strong> · {delta.baseline} → {delta.comparison}<br/>평균 차이 {number(delta.delta)}{delta.percent_change!=null?` · 변화율 ${number(delta.percent_change)}%`:''}</p>)}</div>}
  {result.correlation&&<div className="evaluation-analysis-correlation"><strong>{label(result.correlation.x)} ↔ {label(result.correlation.y)}</strong><dl><div><dt>Pearson 상관계수</dt><dd>{number(result.correlation.pearson)}</dd></div><div><dt>유효한 대응 행</dt><dd>{number(result.correlation.pair_count)}</dd></div><div><dt>제외된 행</dt><dd>{number(result.correlation.excluded_count)}</dd></div></dl><p>같은 데이터 행의 두 값을 짝지어 계산했습니다. 데이터셋의 연결 기준과 측정 단위가 비교 가능한지 확인하세요.</p></div>}
  {result.ml&&<div aria-label="ML 검증 결과"><h3>{label(result.ml.target)}와 여러 요인의 관계</h3><Alert severity={result.ml.status==='validated'?'info':'warning'}>{result.ml.interpretation}</Alert>
   <p>유효 {number(result.ml.valid_rows)}행 · 결측 제외 {number(result.ml.excluded_rows)}행</p>
   {result.ml.validation&&<p>과거 학습 {number(result.ml.validation.train_rows)}행 / 이후 검증 {number(result.ml.validation.test_rows)}행 · 같은 표본 중복 제외 {number(result.ml.validation.purged_rows)}행</p>}
   {result.ml.metrics&&<p>예측 오차: ML {number(result.ml.metrics.mae)} / 단순 기준 {number(result.ml.metrics.baseline_mae)} · R² {number(result.ml.metrics.r2)}</p>}
   {!!result.ml.importance.length&&<><h4>예측에 도움이 된 항목 후보</h4>{result.ml.importance.map(v=><p key={v.column}><strong>{label(v.column)}</strong> · 값을 섞었을 때 오차 증가 {number(v.mae_increase)} (변동 {number(v.std)})</p>)}<small>변수 중요도이며 인과 효과나 영향 비율이 아닙니다. 함께 움직이는 변수는 중요도가 나뉠 수 있습니다.</small></>}
   <details><summary>항목별 통계와 관측 관계</summary>{result.ml.statistics.map(s=><p key={s.column}>{label(s.column)}: 평균 {number(s.mean)}, 표준편차 {number(s.stddev)}{s.pearson_to_target!==null?` · 목표와 상관 ${number(s.pearson_to_target)}`:''}</p>)}{result.ml.groups.map(g=><div key={g.column}><strong>{label(g.column)}</strong>{g.values.map(v=><p key={v.value}>{v.value}: {v.count}행 · 목표 평균 {number(v.target_mean)}</p>)}</div>)}</details>
  </div>}
  <details><summary>계산 요약·조회 근거 보기</summary><p style={{whiteSpace:'pre-wrap'}}>{result.summary}</p></details>
  {!!result.warnings?.length&&<ul className="evaluation-analysis-limitations">{result.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>}
  <Alert severity="info">이 결과는 선택한 범위의 관측값입니다. 그룹 차이나 상관만으로 Recipe·장비가 원인이라고 확정하지 않습니다.</Alert>
 </section>;
}
