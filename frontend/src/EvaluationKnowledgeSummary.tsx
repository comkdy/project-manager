import type {EvaluationTermView} from './semanticCollectionTypes';
import {validEvaluationClaims,relationshipLabels,reportedDisplay} from './evaluationRelationships';
import type {EvaluationExecution} from './evaluationRelationships';
import './evaluation-knowledge-summary.css';

export default function EvaluationKnowledgeSummary({details,terms=[],execution}:{details:Record<string,unknown>;terms?:EvaluationTermView[];execution?:EvaluationExecution|null}){
 const claims=validEvaluationClaims(details.claims)?details.claims:[];
 const states:Record<string,string>={linked:'승인된 데이터 연결',defined:'뜻 확인 · 데이터 연결 필요',explained:'설명 보관 · 데이터 연결 필요',ambiguous:'확인 요청에서 의미 확인',unmapped:'확인 요청에서 의미 확인',deferred:'데이터 연결 미확인',needs_review:'연결 재확인 필요'};
 return <section className="evaluation-knowledge-summary" aria-label="관계 지식과 분석 근거">
  {!execution&&<><h3>사용자가 알려준 관계</h3>{claims.length?<>
   <ul className="evaluation-relationships">{claims.map((c,index)=><li key={index}>
    <strong>{c.source_term} {c.relationship==='co_movement'?'↔':'→'} {c.target_term}</strong><span>{relationshipLabels[c.relationship]}</span>
    <dl><div><dt>진술된 방향</dt><dd>{reportedDisplay(c.direction)}</dd></div><div><dt>진술된 영향량</dt><dd>{reportedDisplay(c.magnitude)}</dd></div></dl>
    <details><summary>사용자 원문 확인</summary><blockquote>{c.quote}</blockquote>{[c.direction,c.magnitude].filter(v=>v&&v.quote!==c.quote).map((v,i)=><blockquote key={i}>{v!.quote}</blockquote>)}</details>
   </li>)}</ul><p>화살표는 사용자가 말한 관계입니다. 승인해도 인과관계가 입증되는 것은 아닙니다.</p>
  </>:<p>관계 단위로 정리되기 전의 기록입니다. 원문의 방향·수치를 새 관계로 추정해 채우지 않았습니다.</p>}</>}
  {!!terms.length&&<details className="evaluation-connections"><summary>실제 데이터 연결 · {terms.filter(t=>t.status==='linked'&&t.available!==false).length}/{terms.length}개 확인</summary><ul>{terms.map(t=><li key={t.term}>
   <strong>{t.term}</strong> · {states[t.status]??'연결 확인 필요'}
   {t.explanation&&<p>{t.explanation}</p>}{t.references.map(r=><p key={r.item_id}>{r.name} · 승인 v{r.version}</p>)}
   {!!t.bindings?.length&&<details><summary>테이블·컬럼·조건 보기</summary><pre>{JSON.stringify(t.bindings,null,2)}</pre></details>}
  </li>)}</ul><p>Inline은 operation + parameter를 한 항목으로 연결합니다. 자연어 설명만 있는 항목은 실제 데이터 연결이 확정된 상태가 아닙니다.</p></details>}
  <h3>데이터로 확인한 결과</h3>
  {execution?.unavailable?<p>현재 분석 근거를 확인할 수 없습니다. 방향·영향량을 채우지 않습니다.</p>:execution?<>
   <p>{({group_compare:'그룹별 관측 비교',correlation:'상관 분석',ml_regression:'ML 예측 분석'} as Record<string,string>)[execution.findings?.method??'']??'보존된 분석 결과'}{execution.captured_at?` · ${new Date(execution.captured_at).toLocaleDateString('ko-KR')}`:''}</p>
   {!!execution.measurement_items?.labels&&<ul>{Object.entries(execution.measurement_items.labels).map(([column,label])=><li key={column}>{label}</li>)}</ul>}
   {execution.findings?.observed_differences.map((d,i)=><p key={i}><strong>{d.item}</strong> · {d.comparison} − {d.baseline}: {d.delta===null?'계산 불가':d.delta.toLocaleString(undefined,{maximumSignificantDigits:6})} (관측 평균 차이)</p>)}
   <p>{execution.findings?.notice??'원본 실행에 보존된 계산 결과입니다. 인과 효과로 해석하지 않습니다.'}</p>
   <details><summary>계산 수치와 원본 실행 근거</summary><p className="evaluation-computation">{execution.summary}</p></details>
  </>:<p>이 기록에는 계산 근거가 없습니다. 승인 후 데이터셋·분석 추천에서 연결과 비교 조건을 확인하면 분석을 시작할 수 있습니다. 결과는 별도 확인 요청에서 승인합니다.</p>}
  <p className="knowledge-help">인과 효과: 미계산 · 분석 결과가 생겨도 사용자 진술의 미정 값은 덮어쓰지 않습니다.</p>
 </section>;
}
