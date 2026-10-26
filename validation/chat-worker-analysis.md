# 챗 통계·ML 실행 검증 — 2026-10-25

## 구현

AI 챗에 `data_analysis` 경로를 추가했다. 사용자 질문·기존 승인 지식·선택한 데이터셋 구조로 계산 방법을 고르고, 실제 계산은 기존 평가 분석 워커가 담당한다. 현재 지원은 그룹별 기술 통계, 두 수치의 Pearson 상관, RandomForest 회귀와 시간/Lot 분리 검증이다. ML 입력 행은 로컬 워커에만 전달하고 계획 모델에는 원천 행을 보내지 않는다.

결과 메시지는 실제 계산값으로 구성하며 조회 시점·데이터셋 버전·표본 수·범위·컬럼 선택 근거와 다음 확인 사항을 함께 표시한다. 설명은 검증된 결과에 기반한 서버 문장으로 구성한다. 인과관계를 주장하지 않으며 표본 부족/기준 예측보다 나쁜 ML은 중요 요인 결론으로 승격하지 않는다.

모호한 연결은 기존 확인 요청에 질문한다. 승인된 답변으로 추천을 재개하면 같은 워커로 이어지고, 답변 근거는 재시도에서도 유지된다. 완료 결과는 자동 출판하지 않는다. 사용자의 검토 요청은 기존 불변 수치 기반 초안으로 연결되고 명시적인 승인을 거쳐야 지식이 된다.

대시보드에서 카드를 고르지 않은 상태에 엉뚱한 첫 번째 데이터셋이 분석 대상으로 붙던 전달 경로도 정리했다. 선택이 없으면 현재 프로젝트 후보에서 찾고, 실제 선택한 데이터셋/카드 필터·기간은 유지한다. 프로젝트 대시보드·데이터 편집 구성은 유지했다.

## 실행 결과

- 핵심/회귀: 아래 백엔드 명령 **312 passed**, exit 0 (92.34초).
- 추가 최종 연결 검사: `test_chat_data_analysis.py test_evaluation_recommendation.py test_chat_history.py` **42 passed**, exit 0. 위 312개와 중복되므로 합산하지 않는다.
- 후속 작업 상태·취소/재시도·계산·승인 설명: `test_activity.py test_operations.py test_job_actions.py test_job_actions_postgres.py test_evaluation_compute.py test_evaluation_narrative.py` **78 passed**, exit 0 (9.33초).
- 마지막 분류 지침/목록 표시 수정 후 `test_chat_data_analysis.py test_activity.py` **29 passed**, exit 0. 데이터 보존 검사도 마지막 앱 재시작 후 다시 통과.
- 전체 화면: `node --experimental-strip-types --test frontend/tests/*.test.mjs` **231 passed**, exit 0. `chat-worker-frontend-tests.txt`.
- TypeScript/Vite 빌드 성공. 기존 Plotly 번들 크기 경고가 있으며 기능 실패는 아니다.
- `generate_chat_feature_contracts.py --check`, `generate_dataset_impact_contract.py --check` 동기화 확인, exit 0.
- `verify_dataset_boundaries_live.py`: 공통/XXXA/XXXB/XXXC 목록 분리, SQL/집계/CTE/UNION/JOIN 범위 제한, 기존 XXXA 카드 7개 조회 성공.
- `verify_chat_worker_preservation.py`: 기존 **32개 테이블 1,551행**의 전체 값 해시 동일. 데이터셋·대시보드·승인 지식·기존 대화 값 불변. 추가 내용은 아래 검증 기록과 감사/조회 컨텍스트뿐이다.

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_chat_data_analysis.py backend/tests/test_chat_workflow.py backend/tests/test_chat_history.py backend/tests/test_chat_grounding.py backend/tests/test_chat_knowledge.py backend/tests/test_chat_knowledge_apply.py backend/tests/test_evaluation_chat_workflow.py backend/tests/test_evaluation_analysis.py backend/tests/test_evaluation_recommendation.py backend/tests/test_evaluation_results.py backend/tests/test_evaluation_natural_flow.py backend/tests/test_evaluation_datasets.py backend/tests/test_knowledge_watch.py backend/tests/test_evaluation_ml.py backend/tests/test_analytics_chat.py backend/tests/test_analytics_proposals.py backend/tests/test_dataset_scope.py backend/tests/test_project_datasets.py backend/tests/test_dataset_reuse.py backend/tests/test_direct_sql_api.py backend/tests/test_query_contracts.py backend/tests/test_python_card_api.py backend/tests/test_gantt.py backend/tests/test_dashboard_period.py backend/tests/test_dataset_impact.py backend/tests/test_period_form_execution.py -q
```

## 실제 DB·화면

`verify_chat_worker_local.py`는 외부 모델을 호출하지 않는다. 명시적으로 검증 계획을 만들고 동일 챗 실행 경로·실제 앱 워커·로컬 API·대화 복원을 연결했다. 표시 대화에는 **[검증 · 모델 미호출]**을 명시했다. 자세한 결과는 `chat-worker-local-results.json`.

- 데이터: XXXA oper B Recipe별 Inline MAIN·EPM PARA3 wafer 단위 결합 v2, **243행**.
- 상관: **0.98231423**. 별도 원천 SQL 상관계수와 오차 1e-12 이내 일치.
- 그룹 비교: inline_main 평균은 RCP1 32.091824, RCP3 32.283723. EPM 평균은 RCP2 1.4135493, RCP3 1.4202694. 인과 효과로 해석하지 않음.
- ML: 과거 학습 **183행**, 미래 검증 **49행**, 검증 Lot과 겹쳐 학습에서 제외 **11행**. 평균 절대 오차 ML **0.00546758**, 중앙값 기준 **0.04171224**; R² **0.9762747**. 중요도 후보 inline_main, recipe_b. 예측 중요도와 인과 효과를 구분.
- 상관 실행 `6545462f-9694-4ec7-bded-28a3c02971de`.
- 그룹 실행 `e4ad4ae5-d2a6-4260-bebe-013025c4ca7f`.
- ML 실행 `cd6c37c9-2cb5-476a-bd06-90191554c2f7`, 대화 `87ed652a-2c61-47ff-a781-501b89269f6b`.
- 확인 요청 `836102cd-3705-43ad-948a-ea2cc5e352cf`, 초안 `2de3ebfe-bd4d-4077-a706-5350594c1e18`는 **승인 대기**로 보존. 실 데이터 지식 승인은 하지 않았으며 승인 이후 축적은 분리된 테스트 DB에서 검증.
- 실제 화면에서 결과 문장·근거 펼치기·지식 미승인 표시·연결된 확인 요청의 승인 필요 상태를 확인했다. 화면: `chat-worker-result.png`.

## 실제 모델 후속 검증 완료

이 문서의 최초 로컬 검증 단계에서는 외부 전송 승인이 없어 모델을 호출하지 않았다. 이후 사용자가 질문·XXXA SQL/컬럼·관련 승인 지식을 현재 OpenRouter 모델에 전송하도록 명시적으로 승인했고, 같은 날 **실제 모델 검증을 완료했다**. 원시 측정 행은 전송하지 않았다.

상관/ML 설정 혼용과 원문에 없는 인용을 수정했다. 상관·그룹 비교·ML·데이터셋 자동 선택·확인 질문을 실제 모델과 앱에서 검사했다. 후속 백엔드 회귀 314개 및 마지막 수정 관련 검사 59개가 통과했다. 실행 ID·수치·승인 대기·외부 전송 확인은 [실제 모델 검증 기록](chat-worker-live-validation.md)에 별도로 남겼다. 위 모델 미호출 기록은 최초 단계의 증거로 유지한다.
