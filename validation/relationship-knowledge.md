# 관계 지식·데이터 연결·계산 근거 분리 — 2026-10-25

## 구현 범위

- 새 자연어 평가 사례는 직접 진술한 관계를 `claims`에 보관한다. 출발/대상 용어는 같은 원문 구절에서 확인하고, 직접 영향 진술·동시 변화 관측·가설을 구분한다. 관계가 있으면 별도 Inline/EPM 수치가 없어도 승인할 수 있다.
- 방향과 영향량은 사용자가 명시한 경우에만 정확한 원문, 답변 ID와 함께 보관한다. 미언급은 null이며, 승인 지식이나 데이터로 원문을 보충하지 않는다. 추가 답변의 방향/수치는 해당 답변의 출처를 별도로 남긴다.
- 관계의 양 끝 용어는 기존 승인 지식 연결과 확인 요청을 거친다. 모델이 용어 목록을 빠뜨려도 관계 항목은 질문 대상에 포함된다. 설명만 있는 상태와 승인된 실제 데이터 매핑을 구분한다.
- 계산 결과는 기존 분석 실행과 결과 승인 경로로 별도 축적한다. 원래 관계의 null 값을 덮어쓰지 않는다. 기준·비교 그룹의 차이는 관측 평균 차이로 표시하며 상관계수나 ML 중요도를 영향량으로 표시하지 않는다.
- 승인된 새 관계가 데이터셋 추천, 분석 준비, 실행, 결과 검토·승인, 사례 검색과 챗 근거에 이어지는 통합 테스트를 추가했다. 수치 연산 자체는 기존 worker를 사용하며 기존 계산 테스트로 검증했다.
- 관계/데이터 연결/계산 근거를 분리해서 표시하고 긴 원본 수치는 펼쳐 확인한다. Inline 항목은 operation + parameter 쌍을 보여준다. 기존 프로젝트·데이터셋·카드 편집 화면은 수정하지 않았다.
- 과거 평가 사례 3건은 소급 재해석하거나 변환하지 않았다. JSON 필드가 비어 있으면 이전 내용과 동일하게 직렬화해 기존 실행 근거 비교와 승인 유효성을 보존한다.

## 실제 모델 검증

` .venv/Scripts/python.exe -X utf8 validation/verify_relationship_provider.py ` — 종료 0, 4개 입력 통과.

현재 설정 모델 `z-ai/glm-5.3-flash`를 사용했다. 합성 자연어와 합성 용어 정의만 보냈고 측정 행은 0건, 업무 DB 쓰기는 0건이다.

1. 과거 지식에 '5 nm 증가'가 있어도 새 문장에 방향/수치가 없으면 null 유지.
2. '2 nm 감소'는 명시된 원문의 방향/수치로 보관.
3. A→B, B→C 진술만 있을 때 A→C 관계를 생성하지 않음.
4. 함께 변했다는 문장의 상관계수 0.9를 영향량으로 사용하지 않음.

초기 실제 응답에서 빈 fields를 null로 반환하거나 원문에 없는 용어/구절 참조를 반환하는 오류를 발견했다. null은 사실을 만들지 않고 생략한다. 원문 연결 또는 형식 검증 실패 시 원문으로 한 번 자동 교정하고, 여전히 실패하면 저장하지 않는다. 인증/권한/한도 오류는 무작정 재시도하지 않는다. 잘못된 출력이나 비밀을 다음 요청에 재전송하지 않는다. 원본은 보존된다.

결과: `relationship-provider.json`, `relationship-provider-response.json`.

## 자동 검증

### 넓은 회귀

```text
.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_evaluation_relationships.py backend/tests/test_evaluation_extraction.py backend/tests/test_semantic_collection.py backend/tests/test_evaluation_cases.py backend/tests/test_evaluation_natural_flow.py backend/tests/test_evaluation_results.py backend/tests/test_evaluation_analysis.py backend/tests/test_evaluation_compute.py backend/tests/test_evaluation_recommendation.py backend/tests/test_evaluation_datasets.py backend/tests/test_evaluation_narrative.py backend/tests/test_evaluation_chat_workflow.py backend/tests/test_evaluation_chat.py backend/tests/test_evaluation_ml.py backend/tests/test_knowledge_watch.py backend/tests/test_chat_data_analysis.py backend/tests/test_chat_workflow.py backend/tests/test_chat_history.py backend/tests/test_chat_grounding.py backend/tests/test_chat_knowledge.py backend/tests/test_chat_knowledge_apply.py backend/tests/test_chat_pivot.py backend/tests/test_analytics_chat.py backend/tests/test_analytics_proposals.py backend/tests/test_dataset_scope.py backend/tests/test_project_datasets.py backend/tests/test_dataset_reuse.py backend/tests/test_direct_sql_api.py backend/tests/test_query_contracts.py backend/tests/test_python_card_api.py backend/tests/test_gantt.py backend/tests/test_dashboard_period.py backend/tests/test_dataset_impact.py backend/tests/test_period_form_execution.py backend/tests/test_source_discovery.py backend/tests/test_dataset_sources.py backend/tests/test_measurement_items.py backend/tests/test_activity.py backend/tests/test_operations.py backend/tests/test_job_actions.py backend/tests/test_job_actions_postgres.py demo/fab/test_generator.py demo/fab/test_schedule.py -q
```

종료 0, **521 passed**. 로그 `relationship-regression.log`.

### 모델 통신 보완 후 변경 경로

```text
.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_collection_transport.py backend/tests/test_evaluation_relationships.py backend/tests/test_evaluation_extraction.py backend/tests/test_evaluation_natural_flow.py backend/tests/test_evaluation_results.py backend/tests/test_semantic_collection.py -q
```

종료 0, **95 passed**. 자동 재시도 상한, 잘못된 참조 실패, 없는 항목 생성 방지, 원문 근거, 자연어 추가 답변, 승인 경합, 기존 결과 불변을 포함한다.

### 마지막 챗 표시 변경

```text
.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_evaluation_relationships.py backend/tests/test_evaluation_narrative.py backend/tests/test_evaluation_cases.py backend/tests/test_evaluation_chat.py -q
```

종료 0, **65 passed**. 위 검증과 중복되므로 합산하지 않는다.

### 화면

```text
node --experimental-strip-types --test frontend/tests/*.test.mjs
# frontend 디렉터리
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

화면 로직 **234 passed**, 타입 검사·배포 빌드 종료 0. 기존 Plotly 묶음 크기 안내와 Starlette 사용 중단 예정 경고는 오류가 아니다.

로컬 앱에서 평가 사례 3건, 기존 ML 결과, 정확한 공정·측정 항목, 원본 수치 펼치기와 인과 효과 미계산 표시를 확인했다. `relationship-computed-ui.png`.

## 데이터 보존

32개 테이블의 기존 **1,758행** 해시를 비교했다. 기존 행 변경·삭제 0, 업무 기록 추가 0. 기존 승인 평가 사례 3건 모두 유효하다. 기존 회귀 테스트가 남기는 감사 이력 13건과 검색 컨텍스트 23건만 추가되어 구체적인 QA 이름·행 생성 시각을 확인했다. 이를 데이터 변경 없이 통과했다고 숨기지 않는다. 실제 데이터셋·카드·프로젝트·승인 지식은 동일하다.

검증 근거: `relationship-knowledge-metadata-before.json`, `relationship-knowledge-metadata-after.json`, `relationship-preservation.json`.

## 범위와 한계

인과 효과 추정기, 실험/준실험 설계, 매개·교란 통제는 이번 단계에 추가하지 않았다. ML 중요도는 공정 변경의 방향이나 영향량을 산출하지 않는다. 분석을 시작하려면 기존 추천 흐름에서 실제 데이터와 표본 대응·비교 조건을 확인해야 하며, 결과의 지식 반영은 기존 확인 요청 승인을 거친다. 문장 해석은 여전히 검토 대상이고 자동 교정은 완전한 의미 정확성을 보장하지 않는다.
