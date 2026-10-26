"""Local, read-only explanation of inspected records. Does not change the app DB."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
audit = json.loads((ROOT/'validation/evaluation-structure-current.json').read_text(encoding='utf-8'))
cases = audit['records']
reported = next(c for c in cases if not c.get('execution'))
computed = [c for c in cases if c.get('execution')]
assert len(cases)==3 and len(computed)==2
assert computed[0]['execution']['result']['data_fingerprint']==computed[1]['execution']['result']['data_fingerprint']
assert computed[0]['execution']['settings']==computed[1]['execution']['settings']
assert all(t['status']=='explained' and t['references']==[] for t in reported['term_links'])
labels = {'operation':'공정','recipe_changes':'Recipe 변경','inline_observations':'Inline 관측',
          'epm_observations':'EPM 관측','conditions':'적용 조건','evidence':'근거','limitations':'한계','analysis_result':'분석 결과'}
esc=lambda v:html.escape(str(v))
def rows(values):
    return ''.join('<tr>'+''.join(f'<td>{v}</td>' for v in row)+'</tr>' for row in values)
field_rows = rows([(f'{esc(label)}<small>{key}</small>',
    esc(reported['content']['details'].get(key) or '(비어 있음)'),
    '<span class="warn-text">같은 문장 반복</span>' if key in {'recipe_changes','inline_observations','epm_observations'} else '원문 구절')
    for key,label in labels.items()])
term_rows = rows([(esc(t['term']),esc(t['explanation']),'<span class="pill amber">설명됨</span><small>정식 데이터 참조 0개</small>') for t in reported['term_links']])
ml_rows=rows([(f'<b>{esc(c["id"][:8])}</b><small>실행 {esc(c["execution"]["id"][:8])}</small>',
    esc(c['execution']['result']['captured_at']),str(c['execution']['result']['row_count']),
    f'{c["execution"]["result"]["ml"]["metrics"]["mae"]:.8f}',
    '<span class="pill">같은 입력·설정</span>') for c in computed])
raw_sections=''.join(f'<details><summary>{esc(c["content"]["name"])} · {esc(c["id"][:8])}</summary><pre>{esc(json.dumps(c,ensure_ascii=False,indent=2))}</pre></details>' for c in cases)
proposed = {'notice':'설명용 제안 · 현재 DB에는 이 구조가 구현되어 있지 않음',
 'scope':'XXXA', 'original_text':'A operation은 B에 영향을 준다',
 'claim':{'relation':'영향을 준다는 사용자 진술','source':'사용자 입력','review_status':'검토 전','causal_evidence_status':'미검증'},
 'source':{'operation_term':'A operation','physical_binding':None,'change_variable':None,'from_value':None,'to_value':None},
 'target':{'term':'B','family':None,'operation':None,'parameter':None,'physical_binding':None},
 'direction':None,'effect_size':None,'effect_interval':None,'supporting_runs':[],
 'questions':['A 공정에서 Recipe·장비·설정 중 무엇이 어떻게 바뀌었나요?', 'B는 어느 Inline 공정+parameter 또는 EPM parameter를 뜻하나요?']}
template=r'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>평가 사례 · 실제 저장 구조 점검</title><style>
:root{--ink:#1e2b38;--muted:#647483;--line:#dae1e6;--paper:#fff;--blue:#1c5a65;--tint:#edf6f5;--amber:#81501a;--cream:#fff6e7}*{box-sizing:border-box}body{margin:0;background:#f5f7f9;color:var(--ink);font:15px/1.7 system-ui,'Malgun Gothic',sans-serif}main{max-width:1120px;margin:auto;padding:38px 30px 80px}h1{font-size:34px;letter-spacing:-1.5px;margin:10px 0 12px;line-height:1.3}h2{font-size:23px;letter-spacing:-.6px;margin:34px 0 12px}h3{font-size:18px;margin:22px 0 10px}p{margin:8px 0 15px}.eyebrow{color:var(--blue);font-size:12px;letter-spacing:1.1px;font-weight:750}.lede{max-width:870px;font-size:17px}.muted,small{color:var(--muted)}small{display:block;font-size:12px;line-height:1.6}.notice{border-left:4px solid var(--blue);background:var(--tint);padding:15px 19px;margin:22px 0}.notice.warn{background:var(--cream);border-color:#d79d4d}.stats{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:20px 0;margin:26px 0}.stats div{padding:0 20px;border-right:1px solid var(--line)}.stats div:first-child{padding-left:0}.stats div:last-child{border:0}.stats strong{font-size:28px;display:block;line-height:1.2}.stats span{font-size:13px;color:var(--muted)}nav{display:flex;gap:5px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin:12px 0 22px}nav button{border:0;background:transparent;color:var(--muted);padding:14px 16px;font:inherit;font-weight:650;cursor:pointer;border-bottom:3px solid transparent}nav button[aria-selected=true]{color:var(--blue);border-color:var(--blue)}button:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #66a8b0;outline-offset:4px}[hidden]{display:none!important}.sheet{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:24px;margin-top:20px}.sheet h2:first-child{margin-top:0}.pill{display:inline-block;border-radius:20px;background:#eaf2f4;color:#315565;padding:3px 10px;font-size:12px;white-space:nowrap}.pill.amber{background:#fff0d5;color:var(--amber)}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px;text-align:left}th{color:var(--muted);font-weight:600;font-size:12px;background:#f7f9fa;white-space:nowrap}td,th{padding:13px 12px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{min-width:130px}td:nth-child(2){min-width:260px}.warn-text{color:var(--amber);font-weight:600}code{font-size:12px;background:#f1f4f6;padding:2px 5px;border-radius:3px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#172b36;color:#e2edf1;padding:20px;border-radius:8px;font:12px/1.8 ui-monospace,Consolas,monospace;max-height:580px;overflow:auto}details{border-top:1px solid var(--line);padding:14px 0}summary{cursor:pointer;color:var(--blue);font-weight:600}blockquote{margin:12px 0;padding:15px 20px;border-left:3px solid #abbfc6;background:#f6f9fa;font-size:17px}.mapping{display:grid;grid-template-columns:1fr 30px 1fr 30px 1fr;align-items:center;margin:20px 0}.node{border-top:3px solid var(--blue);padding:16px;background:var(--tint);min-height:154px}.node b{display:block;font-size:16px}.arrow{text-align:center;color:var(--muted)}.step{display:grid;grid-template-columns:40px 1fr;gap:14px;margin:22px 0}.step .num{font-size:25px;color:var(--blue);font-weight:700}.step h3{margin:0 0 5px}.steps{counter-reset:step}a{color:var(--blue)}.foot{border-top:1px solid var(--line);padding-top:22px;margin-top:30px;font-size:12px;color:var(--muted)}.source-links{display:flex;gap:18px;flex-wrap:wrap}ol{padding-left:22px}li{margin:8px 0}@media(max-width:700px){main{padding:22px 16px 50px}h1{font-size:27px}.sheet{padding:18px}.stats{grid-template-columns:repeat(2,1fr);gap:22px}.stats div{padding:0;border:0}.mapping{display:block}.node{min-height:0}.arrow{padding:5px;transform:rotate(90deg)}nav button{padding:12px 10px;font-size:13px}}
</style></head><body><main>
<div class="eyebrow">PROJECT MANAGER / KNOWLEDGE AUDIT</div>
<h1>평가 사례에 실제로<br>무엇이 저장돼 있나</h1>
<p class="lede">현재 구현은 <b>관측 기록을 정리하고, 관련 데이터로 예측을 검증하는 단계</b>입니다. 공정을 바꾸면 이후 특성이 얼마나 달라지는지 추정하는 <b>인과 효과 분석은 아직 없습니다.</b></p>
<small>2026-10-25 실제 DB·코드 확인 · 합성 DB · 읽기 전용 점검 · 원시 측정 행 조회/외부 모델 호출 없음</small>
<div class="stats"><div><strong>1건</strong><span>원문으로 등록한 평가 사례</span></div><div><strong>2건</strong><span>같은 데이터·설정의 승인 ML 실행</span></div><div><strong>0 / 11</strong><span>사례 용어의 정식 데이터 참조</span></div><div><strong>미구현</strong><span>공정 변경의 인과 효과 추정</span></div></div>
<nav role="tablist" aria-label="점검 내용"><button id="tab-current" role="tab" aria-selected="true" aria-controls="current">실제 저장 내용</button><button id="tab-input" role="tab" aria-selected="false" aria-controls="input">한 줄 지식은 어떻게?</button><button id="tab-effect" role="tab" aria-selected="false" aria-controls="effect">영향 분석의 차이</button><button id="tab-original" role="tab" aria-selected="false" aria-controls="original">원문·JSON 직접 보기</button></nav>
<section id="current" role="tabpanel" aria-labelledby="tab-current">
<div class="notice"><b>화면만의 문제가 아닙니다.</b><br>저장 형식에도 “변경 대상 / 변경 전후 / 영향을 받는 측정 항목 / 관계의 종류 / 효과 크기”가 독립된 항목으로 정의되어 있지 않습니다.</div>
<div class="sheet"><span class="pill">실제 승인 사례 · ceb1f7d8 · v1 · XXXA</span><h2>@@CASE_NAME@@</h2><p>사용자가 한 줄 지식을 넣은 예제가 아니라, 제가 절차 검증을 위해 등록했던 긴 관측 보고서입니다. 현재 사례들이 복잡해 보이는 이유이기도 합니다.</p><h3>DB에 저장된 필드를 그대로 펼치면</h3><div class="table-wrap"><table><thead><tr><th>저장 항목</th><th>실제 저장값</th><th>확인 결과</th></tr></thead><tbody>@@FIELDS@@</tbody></table></div><p class="muted">원문 왜곡을 줄이려고 LLM이 새 문장을 쓰는 대신 원문 구절을 골라 넣도록 구현했습니다. 그 결과 Recipe·Inline·EPM 항목에 동일한 복합 문장이 중복되었습니다.</p>
<h3>용어의 뜻은 있으나 데이터 연결은 설명문에 머뭅니다</h3><p>아래 11개 용어는 모두 <code>status: explained</code>, <code>references: []</code>입니다. 예를 들어 Inline MAIN의 뜻에 <b>oper B CD / MAIN</b>이 명시되어 있습니다. “아무 정보도 없다”는 뜻은 아닙니다. 다만 승인된 별도 용어 정의·물리 매핑으로 연결된 상태가 아닙니다.</p><div class="table-wrap"><table><thead><tr><th>사용한 용어</th><th>보존된 자연어 설명</th><th>현재 연결 상태</th></tr></thead><tbody>@@TERMS@@</tbody></table></div><p class="warn-text">따라서 기존 화면/답변의 “데이터 연결 미확인”은 “사용자가 공정명을 설명하지 않았다”와 구분해야 합니다. 이 사례는 설명문에 공정명이 있지만 정식 데이터 참조가 없습니다.</p></div>
<div class="sheet"><h2>분석할 때는 별도 데이터셋 SQL로 연결했습니다</h2><p>아래는 <b>실제 실행의 표본·시간 연결</b>입니다. 이 화살표 자체가 검증된 인과관계는 아닙니다.</p><div class="mapping"><div class="node"><small>공정 이력</small><b>wafer_his</b>operation = oper B<br>recipe → recipe_b</div><div class="arrow">→</div><div class="node"><small>Inline 측정 항목</small><b>oper B CD / MAIN</b>value의 대응 위치 평균<br>→ inline_main</div><div class="arrow">→</div><div class="node"><small>EPM 측정 항목</small><b>PARA3</b>value의 동일 위치 평균<br>→ epm_para3</div></div><p><b>1행 = 한 wafer.</b> 같은 lot_cd + lot_id + wf_id + die_x + die_y를 연결하고, 공정 시각 ≤ Inline 시각 &lt; EPM 시각인 위치들의 값을 평균했습니다. 그 결과 243행으로 계산했습니다.</p><p><code>recipe_b, inline_main</code>으로 <code>epm_para3</code>를 예측했습니다. Recipe 변경 전후의 효과를 분리하도록 설계한 실험은 아니며, 새로 추가한 <code>eqp_id</code>도 이 두 실행에는 포함되지 않았습니다.</p><details><summary>실행에 고정된 데이터셋 SQL 보기</summary><pre>@@SQL@@</pre></details></div>
<div class="sheet"><h2>승인된 ML 2건은 같은 분석을 다시 실행한 기록입니다</h2><div class="table-wrap"><table><thead><tr><th>지식 / 실행 ID</th><th>조회 시각 (UTC)</th><th>행 수</th><th>평균 예측 오차</th><th>비교</th></tr></thead><tbody>@@ML_ROWS@@</tbody></table></div><p>원본 입력 해시 <code>@@FINGERPRINT@@</code>, 데이터셋 v2, 설명 변수와 목표, ML 수치가 같습니다. 실행 이력은 보존할 수 있지만, 이를 <b>독립적인 근거 2개로 누적해서 신뢰도를 높이면 안 됩니다.</b></p><p class="muted">분석 결과 지식은 <code>analysis_result</code>에 긴 계산 요약을 저장하고 <code>conditions</code>에 설정 JSON을 문자열로 저장합니다. 이 때문에 사용자가 읽고 싶은 영향 관계보다 계산 기록이 앞에 보입니다.</p></div>
</section>
<section id="input" role="tabpanel" aria-labelledby="tab-input" hidden>
<div class="notice warn"><b>아래는 필요한 구조를 설명한 제안입니다.</b> 현재 앱에 구현됐거나 실제 모델로 이 예문을 등록·분석한 결과가 아닙니다.</div>
<blockquote>“A operation은 B에 영향을 준다.”</blockquote>
<div class="step"><div class="num">1</div><div><h3>원문과 적용 범위를 보존합니다</h3><p>입력 자체는 한 줄이면 충분합니다. 처음에는 <b>사용자가 알려준 영향 관계</b>로 저장하고, 인과 검증 완료나 효과 수치를 만들어 넣지 않습니다.</p></div></div>
<div class="step"><div class="num">2</div><div><h3>관계와 실제 데이터 항목을 따로 정리합니다</h3><p>관계는 <b>A의 어떤 변경 → B의 어떤 측정</b>입니다. A에서 바뀐 것이 Recipe인지 장비인지, B가 어느 Inline 공정+parameter인지 또는 EPM parameter인지 기존 승인 지식에서 찾습니다. 확실한 연결만 사용하고 모르는 칸은 비웁니다.</p></div></div>
<div class="step"><div class="num">3</div><div><h3>모호한 두 가지만 확인 요청에서 묻습니다</h3><p>“A 공정에서 무엇을 무엇으로 바꿨나요?”<br>“B는 어느 Inline 공정+parameter 또는 EPM parameter인가요?”</p><p>기간·장비·비교군 등을 처음부터 전부 입력하게 하지 않습니다. 관계 지식은 먼저 보존하고, 실제 효과 분석을 시작할 때 필요한 조건만 이어서 확인합니다.</p></div></div>
<div class="step"><div class="num">4</div><div><h3>사용자 진술·관측·효과 추정을 구분해 쌓습니다</h3><p>원문을 정리한 관계와 실행한 분석 결과를 서로 연결합니다. 검토 승인은 “이 기록을 업무 지식으로 사용해도 된다”는 확인이며, 그 자체로 인과관계가 입증되는 것은 아닙니다.</p></div></div>
<div class="sheet"><h2>한 줄 입력을 구조화한다면 이런 모양입니다</h2><p>비어 있는 항목은 오류가 아니라 아직 확인하지 못한 내용입니다. <b>B를 임의로 특정 EPM parameter로 해석하지 않습니다.</b></p><pre>@@PROPOSED@@</pre></div>
<p class="muted">현재 구현은 공정과 Inline/EPM 관측 문구를 요구하는 평가 보고서 형식이므로, 이러한 일반적인 한 줄 영향 지식을 다루기에는 구조가 좁습니다.</p>
</section>
<section id="effect" role="tabpanel" aria-labelledby="tab-effect" hidden>
<div class="sheet"><h2>지금 계산한 수치는 무엇인가</h2><div class="table-wrap"><table><thead><tr><th>현재 값</th><th>정확한 의미</th><th>이 값으로 말할 수 없는 것</th></tr></thead><tbody><tr><td>상관 0.982314</td><td>Inline oper B CD / MAIN과 EPM PARA3가 함께 움직인 정도</td><td>Recipe 변경이 EPM 변화의 원인이라는 결론</td></tr><tr><td>중요도 0.04767419</td><td>Inline 값을 섞어 그 정보를 깨뜨렸을 때 모델의 예측 오차가 증가한 양</td><td>Inline·Recipe를 바꾸면 EPM이 그만큼 증가한다는 뜻</td></tr><tr><td>ML 예측 오차 0.00546758</td><td>미래 검증 표본에서 EPM 예측이 실제 관측값과 어긋난 평균 크기</td><td>Recipe 변경의 효과 크기 또는 효과의 신뢰구간</td></tr></tbody></table></div><p>이 구분은 <a href="https://scikit-learn.org/stable/modules/permutation_importance.html" target="_blank" rel="noopener">scikit-learn의 변수 중요도 정의</a>와 같습니다. 모델의 예측 의존도와 공정 변경 효과는 다른 분석 대상입니다.</p></div>
<div class="sheet"><h2>원하신 질문은 이렇게 정의해야 합니다</h2><blockquote>“같은 대상·비슷한 선행 조건에서 oper B의 Recipe를 RCP1에서 RCP3로 바꾸면, 이후 Inline oper B CD / MAIN과 EPM PARA3는 평균적으로 얼마나 달라질까?”</blockquote><div class="table-wrap"><table><thead><tr><th>분석에 필요한 항목</th><th>현재 상태</th></tr></thead><tbody><tr><td>변경 전 → 변경 후</td><td>RCP1 → RCP3 같은 비교를 분석 목표로 명시해야 함</td></tr><tr><td>측정 대상·단위·시간</td><td>공정+parameter와 wafer/위치/시간 연결은 있으나, 측정 단위는 사례에서도 미확인</td></tr><tr><td>변경 외에 달랐던 조건</td><td>제품, Lot, 장비 상태, 시점 등의 영향을 구분할 비교 설계가 없음</td></tr><tr><td>공정 변경의 평균 효과·불확실성</td><td><b>현재 미계산</b></td></tr><tr><td>비교가 성립하는지·다른 조건에서 유지되는지</td><td>인과 분석의 가정 검증·민감도 검사는 미구현</td></tr></tbody></table></div></div>
<div class="sheet"><h2>복잡한 관계를 단순한 화살표로 확정하지 않습니다</h2><p><b>Recipe → Inline → EPM</b>은 검토할 경로 가설입니다. Recipe가 EPM에 직접 영향을 주는 경로, 장비·제품 조건이 양쪽에 영향을 주는 경로도 있을 수 있습니다. Inline이 Recipe 변화의 결과라면, EPM의 전체 영향을 볼 때 Inline까지 무조건 고정하면 그 경로의 영향을 제외할 수 있습니다.</p><p>따라서 “Recipe의 전체 영향”과 “Inline을 거친 영향”을 별도 질문으로 정하고, 데이터와 가정이 충분할 때만 추정해야 합니다. 충분하지 않으면 부족한 비교 조건이나 추가 평가를 제안해야 합니다.</p><p><a href="https://www.pywhy.org/dowhy/main/user_guide/causal_tasks/estimating_causal_effects/index.html" target="_blank" rel="noopener">DoWhy: 가정 → 효과 식별 → 추정 → 검증</a> · <a href="https://www.pywhy.org/dowhy/main/user_guide/causal_tasks/quantify_causal_influence/mediation_analysis.html" target="_blank" rel="noopener">직접·간접 효과의 구분</a></p></div>
<div class="notice"><b>앱이 최종적으로 보여줄 답</b><br>무엇을 어떻게 바꿨는지 / 어떤 측정 항목이 얼마나 변했는지 / 어떤 조건에서 비교했는지 / 효과 추정의 불확실성 / 원문·데이터·분석 근거.<br>효과를 추정하지 않았다면 “인과 효과 미계산”을 명확히 표시해야 합니다.</div>
</section>
<section id="original" role="tabpanel" aria-labelledby="tab-original" hidden><h2>원문과 실제 저장 JSON</h2><p>읽기 전용으로 가져온 평가 사례·답변·검토 근거·분석 설정입니다. 원천 측정 행은 포함하지 않았습니다. 일부 큰 필드는 접어두었습니다.</p>
<details open><summary>최초 등록한 원문</summary><pre>@@ORIGINAL@@</pre></details><details><summary>확인 요청에 보완한 답변</summary><pre>@@FOLLOWUP@@</pre></details>
@@RAW_SECTIONS@@
<p><a href="../validation/evaluation-structure-current.json">읽기 전용 점검 JSON 파일 열기</a></p></section>
<div class="foot">이번 작업은 현재 구조를 설명하기 위한 점검입니다. 평가 사례·승인 상태·데이터셋·앱 기능을 변경하지 않았습니다. 현재 저장 구조의 근거: EvaluationDetails, evaluation_extraction, evaluation_terms, evaluation_results, evaluation_ml.</div>
</main><script>
const tabs=[...document.querySelectorAll('[role="tab"]')];function choose(tab){tabs.forEach(t=>{let selected=t===tab;t.setAttribute('aria-selected',String(selected));document.getElementById(t.getAttribute('aria-controls')).hidden=!selected;});}tabs.forEach((tab,i)=>{tab.addEventListener('click',()=>choose(tab));tab.addEventListener('keydown',e=>{if(['ArrowRight','ArrowLeft'].includes(e.key)){e.preventDefault();let next=tabs[(i+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length];choose(next);next.focus();}})});
</script></body></html>'''
replacements = {'CASE_NAME':esc(reported['content']['name']),'FIELDS':field_rows,'TERMS':term_rows,
    'SQL':esc(computed[0]['execution']['dataset']['spec']['raw_sql']),'ML_ROWS':ml_rows,
    'FINGERPRINT':esc(computed[0]['execution']['result']['data_fingerprint']),
    'PROPOSED':esc(json.dumps(proposed,ensure_ascii=False,indent=2)),
    'ORIGINAL':esc(reported['answers'][0]['text']),'FOLLOWUP':esc(reported['answers'][1]['text']),
    'RAW_SECTIONS':raw_sections}
for key,value in replacements.items():template=template.replace('@@'+key+'@@',value)
assert '@@' not in template
output=ROOT/'design/evaluation-case-audit.html'
output.write_text(template,encoding='utf-8')
print('Created',output,'with 3 actual cases and a clearly separated proposed structure')
