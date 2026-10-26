"""Write a readable report from verified application results."""
import json
from pathlib import Path
from live_case_walkthrough import load

s=load(); a=s['analysis']; result=a['result']; ml=result['ml']
assert a['status']=='completed' and a['review']['status']=='approved'
answer=s['chat']['messages'][-1]['evaluation_cases']
refs={c['id'] for c in answer['cases']}
assert {s['case_approval']['item_id'],a['review']['item_id']}<=refs
assert any(c.get('execution_evidence') for c in answer['cases'] if c['id']==a['review']['item_id'])
reduction=100*(1-ml['metrics']['mae']/ml['metrics']['baseline_mae'])
report=f'''# 실제 평가 사례 → 분석 → 지식 → 채팅 실행 기록

2026-10-25, 현재 실행 중인 Project Manager와 `wafer_fab_demo`에서 직접 진행했습니다.
이 DB는 **합성 데이터**입니다. 실제 제조 공정의 인과 효과나 합격 판정을 검증한 결과가 아닙니다.

## 등록한 사례

**XXXA oper B Recipe별 Inline MAIN·EPM PARA3 관계**

XXXA에서 oper B 공정의 Recipe, 같은 wafer·측정 위치의 Inline MAIN과 EPM PARA3를 연결했습니다.
적용 범위와 자연어로 등록하고, AI가 물어본 용어를 자연어로 보완한 뒤 승인했습니다.
다른 기존 평가 사례의 대기 항목은 승인하지 않았습니다.

| 단계 | 실제 처리 결과 |
|---|---|
| 사례 등록·설명 보완 | 자연어 원문 등록 → 용어 질문 → 자연어 답변 → AI 구조화 |
| 사례 승인 | 승인 사례 v1로 저장 |
| 데이터셋 추천 | 기존 데이터셋 검토 후 새 결합 데이터셋 생성안 제안 |
| 검토·저장 | 마지막 측정·NULL·시간 순서 기준 보정, 기존 데이터셋 작성 기능으로 저장 |
| 실행 성능 보정 | 중복 원천 읽기를 줄인 v2 저장; 원본 통계와 동일함 확인 |
| 데이터셋 재추천 | AI가 만들어진 v2 데이터셋을 재사용하도록 추천 |
| 분석 설정 | AI가 EPM PARA3를 목표, Inline MAIN·Recipe를 설명 항목으로 하는 ML 회귀 추천 |
| 실제 실행 | RandomForest 회귀, Lot·시간 분리 검증, 완료 |
| 결과 검토·승인 | 확인 요청 화면에서 결과를 검토·승인, 원래 사례를 참조하는 지식 v1 생성 |
| AI 챗 | 원래 사례와 승인된 ML 결과 2건 조회, 계산 근거·추가 분석 제안 표시, 대화 저장 |

## 분석 데이터의 기준

- 범위: XXXA, 현재 저장된 전체 대상 데이터. 대시보드의 WW40 기간 조건은 적용하지 않았습니다.
- 공정: `wafer_his.operation='oper B'`의 Recipe.
- Inline: `inline_data.operation='oper B CD'`, `parameter='MAIN'`.
- EPM: `epm_data.parameter='PARA3'`.
- 각 입력의 같은 wafer·측정 위치에서 마지막 행을 선택하고, 동시각은 `record_id`로 구분했습니다.
- 마지막 값이 NULL이면 과거 측정으로 대체하지 않았습니다.
- 같은 wafer·위치만 연결하고 공정 완료 ≤ Inline < EPM 시간 순서를 검사했습니다.
- 대응 위치의 평균을 산출해 **wafer당 한 행**으로 분석했습니다.
- **20 Lot, 243 wafer, 11,805개 대응 위치**. wafer당 46~49개 위치.
- EPM 시각: UTC 2026-08-25 00:39:07 ~ 2026-10-19 00:59:07.

## 실제 결과

| 지표 | 결과 |
|---|---:|
| Inline MAIN ↔ EPM PARA3 Pearson 상관 | {s['baseline'][0]['pearson_r']:.6f} |
| 학습 표본 | 15 Lot · 183 wafer |
| 이후 시점 검증 표본 | 5 Lot · 49 wafer |
| Lot 중복 방지를 위해 학습에서 제외 | 11 wafer |
| ML 평균 절대 예측 오차 | {ml['metrics']['mae']:.8f} |
| 학습 중앙값만 쓴 기준 예측 오차 | {ml['metrics']['baseline_mae']:.8f} |
| 기준 대비 오차 감소 | {reduction:.1f}% |
| 검증 R² | {ml['metrics']['r2']:.6f} |

| Recipe | wafer 수 | Inline 평균 | EPM 평균 |
|---|---:|---:|---:|
'''
for g in s['groups']:
    report+=f"| {g['recipe_b']} | {g['wafers']} | {g['inline_mean']:.6f} | {g['epm_mean']:.6f} |\n"
report+='''
이 모델에서 Inline MAIN의 검증 값을 섞으면 평균 오차가 약 **0.047674**, Recipe를 섞으면 약 **0.002541** 증가했습니다.
따라서 이 범위에서는 Inline MAIN이 EPM PARA3 예측에 큰 정보를 주고, Recipe가 그보다 작은 정보를 더했습니다.
이는 변수의 인과 영향 비율이 아닙니다. 상관된 변수·Lot 편차·측정 시점이 함께 작용할 수 있습니다.
Recipe별 EPM 평균도 단조롭게 증가하지 않아 Recipe 번호가 커질수록 EPM이 변한다고 결론 내리지 않았습니다.
장비 컬럼과 물리 단위·합격 기준은 없어 이번 결과로 평가하지 않았습니다.

## AI 챗에서의 활용

실제로 보낸 질문:

> XXXA의 oper B Recipe·Inline MAIN과 EPM PARA3에 대해 방금 승인한 평가 사례와 ML 분석 결과를 찾아줘. 어떤 항목이 EPM PARA3와 관련이 컸고, 다음에는 무엇을 확인하면 좋을까? 승인된 지식의 분석 범위를 기준으로 설명하고, 인과관계가 확인된 것과는 구분해줘.

실제 답변은 관련 사례 2건을 찾아 **과거 평가 관측**과 **승인된 분석 결과**로 구분했습니다.
승인된 계산 결과에는 위 수치·데이터셋 버전·계산 시점·원본 분석 열기가 연결됩니다.
추가 제안은 재현 여부 비교, 장비·기간별 비교(장비 정보 필요), 계산한 관계의 적용 범위 확인입니다.
이번 채팅에서는 재계산하지 않고 승인 지식을 조회했습니다.

현재 채팅은 **근거 카드와 추가 분석 제안 중심**입니다. 어떤 요인이 중요한지 자유로운 한 문장으로 종합 설명하기보다는,
펼친 근거에서 승인된 계산 수치와 한계를 보여주는 방식임을 실제 화면에서 확인했습니다.

![실제 AI 채팅에서 조회된 승인 지식](../.local/live-case-chat.png)

![채팅에서 펼친 실제 ML 결과](../.local/live-case-chat-metrics.png)

## 도중 발견하고 처리한 사항

1. AI 데이터셋 초안의 NULL 처리와 시간 대응을 등록된 사례 기준으로 검토 보정했습니다. 완전 무검토 자동 실행은 아니었습니다.
2. AI 분석 추천이 계산에 쓰지 않는 컬럼까지 섞어 검증에 실패했습니다. 역할 규칙을 명확히 하고, 검증 실패 시 AI가 한 차례 형식을 고치도록 수정했습니다. 근거 검증은 유지했습니다.
3. 첫 분석은 조회 시간 초과로 실패했습니다. 같은 원천을 반복 읽던 쿼리를 최적화한 뒤 전체 표본·위치 수·상관계수가 같음을 확인하고 최신 버전으로 다시 실행했습니다. 실패 이력은 남아 있습니다.

추천·데이터셋·분석·결과 승인 회귀 검증 47개, 기존 채팅·평가 지식·ML 검증 36개, 합계 **83개 통과**했습니다.
원천 데이터와 기존 프로젝트·카드는 수정하지 않았습니다. 이번 사례용 데이터셋과 지식·실행·대화 기록이 추가됐습니다.
자동 지속 분석은 켜지 않았습니다.

## 앱에서 찾아보기

- **AI 지식 → 평가 사례**: 원래 사례와 승인된 분석 결과.
- **AI 지식 → 확인 요청 → XXXA → 처리된 요청**: 등록 답변과 결과 승인 과정.
- **프로젝트 → XXXA → 데이터 관리**: `XXXA oper B Recipe별 Inline MAIN·EPM PARA3 wafer 단위 결합` v2.
- **XXXA → AI 채팅 → 이전 대화**: 위 질문으로 시작하는 대화.

실행 식별자는 아래와 같습니다. 일반 사용에는 위 화면 이름으로 찾으면 됩니다.
'''
report+=f'''
- 원래 사례: `{s['case_approval']['item_id']}` v1
- 데이터셋: `{s['dataset_apply']['dataset_id']}` v2
- 완료 분석: `{a['id']}`
- 승인 결과 지식: `{a['review']['item_id']}` v1
- 결과 확인 요청: `{a['review']['question_id']}`
- 저장된 대화: `{s['chat_id']}`
'''
(Path(__file__).parent/'live-case-walkthrough.md').write_text(report,encoding='utf-8')
print(json.dumps({'approved':True,'chat_retrieved_both_knowledge_items':True,'report':'validation/live-case-walkthrough.md'},ensure_ascii=False))
