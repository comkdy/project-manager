import {Button,Chip} from '@mui/material';
import {evaluationSuggestions,evaluationNarrative} from './evaluationCases';
import type {ChatEvaluationCases as EvaluationAnswer,EvaluationSuggestion,EvaluationNarrativeStatement} from './evaluationCases';
import './chat-evaluation-cases.css';
import EvaluationKnowledgeSummary from './EvaluationKnowledgeSummary';

const fields=[['operation','공정'],['recipe_changes','Recipe 평가'],['inline_observations','Inline 관측'],['epm_observations','EPM 관측'],['analysis_result','승인된 계산 결과'],['conditions','평가 조건'],['evidence','근거'],['limitations','해석 범위']] as const;

export default function ChatEvaluationCases({value,disabled,onSuggest,onExecute,onOpenAnalysis,onOpenKnowledge}:{value:EvaluationAnswer;disabled:boolean;onSuggest:(text:string)=>void;onExecute:(suggestion:EvaluationSuggestion)=>void;onOpenAnalysis:(id:string)=>void;onOpenKnowledge:()=>void}){
 const suggestions=evaluationSuggestions(value);
 const narrative=evaluationNarrative(value);
 const statement=(item:EvaluationNarrativeStatement,index:number)=><div className="chat-evaluation-paragraph" key={index}>
  <p>{item.text}</p>
  <details className="chat-evaluation-citations"><summary>이 설명의 근거 확인</summary>{item.citations.map((citation,i)=>{
   const source=value.cases.find(c=>c.id===citation.case_ref.id&&c.version===citation.case_ref.version);
   return <div key={i}><strong>{citation.case_name} · v{citation.case_ref.version}</strong><small>{citation.label}</small><blockquote>{citation.quote}</blockquote>
    {source?.execution_evidence&&!source.execution_evidence.unavailable?<Button size="small" onClick={()=>onOpenAnalysis(source.execution_evidence!.run_id)}>근거 분석 열기</Button>:<Button size="small" onClick={onOpenKnowledge}>AI 지식에서 확인</Button>}
   </div>;
  })}</details>
 </div>;
 const cards=<>{value.cases.map(item=><article className="chat-evaluation-case" key={`${item.id}:${item.version}`}>
   <div className="chat-evaluation-title"><strong>{item.name}</strong><Chip size="small" label={item.execution_evidence?'승인된 분석 결과':item.details.claims?.length?'사용자 관계 지식':'과거 평가 관측'}/></div>
   <small>{item.dataset?.name??(item.case_context?.project_id?'프로젝트 평가 사례':'공통 평가 사례')} · 사례 버전 {item.version}</small>
   {!!item.match_reasons.length&&<p className="chat-evaluation-match">관련 근거: {item.match_reasons.join(' · ')}</p>}
   <p>{item.description}</p>
   <details><summary>평가 조건과 근거 보기</summary><EvaluationKnowledgeSummary details={item.details} execution={item.execution_evidence}/><dl>{fields.map(([key,label])=>item.details[key]?<div key={key}><dt>{label}</dt><dd>{item.details[key]}</dd></div>:null)}</dl>
    {item.execution_evidence&&<div className="chat-evaluation-execution"><strong>계산 실행 근거</strong><p>계산 방법 {item.execution_evidence.method_version??'확인 필요'} · 데이터셋 버전 {item.execution_evidence.dataset_version??item.dataset?.version??'확인 필요'}{item.execution_evidence.captured_at?` · 조회 ${new Date(item.execution_evidence.captured_at).toLocaleString('ko-KR')}`:''}</p>{!item.details.analysis_result&&<p>{item.execution_evidence.summary}</p>}{item.execution_evidence.unavailable?<p>현재 이 실행 근거를 열 수 없습니다.</p>:<Button size="small" onClick={()=>onOpenAnalysis(item.execution_evidence!.run_id)}>근거 분석 열기</Button>}</div>}
    {!!item.limitations.length&&<ul>{item.limitations.map((text,i)=><li key={i}>{text}</li>)}</ul>}
   </details>
   {suggestions.filter(s=>s.case_ref.id===item.id&&s.case_ref.version===item.version).map(s=><div className="chat-evaluation-suggestion" key={s.id}>
    <strong>{s.title}</strong><p>{s.reason}</p><Button size="small" variant="contained" disabled={disabled} onClick={()=>onExecute(s)}>현재 데이터로 분석</Button> <Button size="small" variant="outlined" disabled={disabled} onClick={()=>onSuggest(s.question)}>{s.action} · 질문에 넣기</Button>
   </div>)}
  </article>)}</>;
 return <section className="chat-evaluation-cases" aria-label="관련 평가 사례와 추가 분석 제안">
  {narrative?<section className="chat-evaluation-narrative" aria-label="승인 근거에 대한 AI 설명">
   <small>승인된 근거를 바탕으로 한 AI 해석</small>
   {narrative.findings.map(statement)}
   <h4>다음에 확인하면 좋은 점</h4>{narrative.next_checks.map(statement)}
   <h4>해석할 때 함께 볼 점</h4>{narrative.caveats.map(statement)}
   <p className="chat-evaluation-scope">{narrative.notice}</p>
  </section>:value.narrative?.status==='unavailable'?<p role="status">{value.narrative.message}</p>:null}
  {narrative?<details className="chat-evaluation-evidence-list"><summary>근거 사례 {value.cases.length}건 · 조건 확인 및 추가 분석</summary>{cards}</details>:cards}
  {!!value.analyses?.length&&<div className="chat-evaluation-runs" aria-label="이 제안에서 준비한 분석">{value.analyses.map(run=><div key={run.id}>{run.available===false?<p>{run.explanation??'이 분석을 현재 범위에서 확인할 수 없습니다.'}</p>:<Button size="small" variant="outlined" onClick={()=>onOpenAnalysis(run.id)}>현재 데이터 분석 열기</Button>}</div>)}</div>}
  {!!value.warnings.length&&<ul className="chat-evaluation-notices">{value.warnings.map((text,i)=><li key={i}>{text}</li>)}</ul>}
  <Button size="small" onClick={onOpenKnowledge}>AI 지식 열기</Button>
 </section>;
}
