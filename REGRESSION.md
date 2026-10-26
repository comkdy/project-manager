# 연결 기능 회귀 검증

## 회사 Parquet 반입·다른 컬럼명·프로젝트 경계 (2026-10-26)

- 변경: `scripts/import_parquet_demo.py`, `backend/pyproject.toml`, `backend/requirements.lock`, `chat_workflow.py`, 시연 가이드. `lot_cd`와 모든 원래 컬럼명·값을 유지한다. 새 DB에만 반입하고 등록/활성화를 구분한다.
- 연결: Parquet → PostgreSQL 읽기 전용 원천 → 프로젝트별 데이터셋 → JOIN/카드 → 자연어 확인 요청·승인 → 범용 통계·ML → 결과 승인 → 지식 검색·대화 복원.
- 채팅에서 고정 스키마용 PARA 도구를 잘못 선택하더라도 회사 스키마가 지원되지 않거나 데이터셋/카드가 선택되어 있으면 원문을 범용 워커에 전달한다. 스키마 정보가 없는 분류 재시도도 유지한다. 기존 query/dataset/card 경로와 프로젝트 화면은 유지한다.
- 연결 근거: `rg -n 'analysis_grounding|data_analysis|lot_cd|load_active_source' backend/app scripts/import_parquet_demo.py`.
- 전파 검사: `python validation/company_demo_qa.py run -m pytest backend/tests -q --tb=short` → **1,402개 통과, exit 0**. 별도 검증 DB에서 실행했다. 중간에 드러난 오래된 기대값(추가 컬럼·프로젝트 JOIN·삭제된 백업·분류 컨텍스트)을 현재 계약에 맞게 수정했고, 빈 스키마 정보 오류를 코드에서 수정했다. 채팅 관련 59개 재검증도 통과했으며 중복 합산하지 않는다.
- 화면: `node --experimental-strip-types --test frontend/tests/*.test.mjs` → **234개 통과, exit 0**. TypeScript/Vite 빌드 exit 0. 실제 별도 검증 화면에서 프로젝트 범위, 원본/추가 데이터셋, 120행 카드와 기존 편집기, 승인 지식 표시 확인.
- 실제 DB 수용: `python validation/company_demo_qa.py flow validation/verify_company_demo_flow.py` → exit 0. 240/480/240행의 다중 파일·다른 컬럼명·값 보존, XXXA/XXXB 분리, 상관/그룹/ML 각 120행, 승인 지식 검색·대화 복원 확인. 반입 CLI 등록 및 `--register-only --activate`도 exit 0.
- 보존: `python validation/company_demo_qa.py preserve` → 기존 사용자 DB 32개 테이블 1,794행의 개수와 해시 일치, exit 0. 활성 DB는 바꾸지 않았다.
- 미검증: 사내 실제 Spark/모듈 실행과 사내 모델 API. 이번 계획·자연어 추출은 검증용 모델 응답이며 외부 모델/원시 측정 행 전송은 없다. 실제 모델 품질까지 검증했다고 표현하지 않는다.
- 상세 결과와 명령: [validation/company-demo-readiness.md](validation/company-demo-readiness.md). 반입·스코프·분류 변경 시 위 후속 흐름을 재검증한다.

## 원천 장비 컬럼·공정별 측정 항목 (2026-10-25)

- 변경 연결: 합성 wafer_his 스키마/생성기 → 원천 자동 갱신 → 프로젝트 데이터셋 → 분석 항목 식별/추천 → 완료 결과·확인 요청·승인 지식 → 챗 설명/대화 복원.
- Inline은 operation+parameter 쌍으로 식별하며, SQL의 실제 공정·PARA 제한만 근거로 사용한다. 공정이 미확정된 MAIN 일반화와 다른 JOIN 원천의 공정명을 붙이는 동작을 차단한다.
- 완료된 실행은 원천 컬럼 추가 시 기존 사용 컬럼과 자료 정의가 보존된 경우만 유지한다. 삭제·타입·연결·프로젝트·권한 변경은 기존 차단 유지. 미완료 작업은 원천 변경을 다시 확인한다.
- 백엔드/데모 회귀 448개, 실모델 후속 설명 수정 뒤 관련 57개, 화면 232개 통과(중복 합산하지 않음). TypeScript/Vite 빌드 및 실제 카드 7개 조회 성공.
- 원천 wafer_his 7,627행 기존 값 보존, 공통·XXXA·XXXB·XXXC 원천 버전 4개 갱신. 기존 앱 1,711행은 요청된 원천 메타데이터 1행 외 모두 보존.
- 실제 OpenRouter 답변이 승인된 ML을 인용하면서 `Inline oper B CD / MAIN`으로 설명하고, 원시 행 미전송·대화 복원 확인. 상세/실행 로그: `validation/wafer-equipment-and-items.md`.

## 챗에서 현재 데이터 통계·ML 실행 (2026-10-25)

- 후속 실제 승인·지식 재사용: 사용자가 지정한 ML 결과 한 건을 검토 API로 승인하고, 실제 `z-ai/glm-5.3-flash` 답변 문단이 새 지식 v1의 계산 결과를 인용함을 확인했다. `approve_chat_worker_result.py`, `verify_approved_chat_worker_knowledge.py`, `verify_chat_worker_approval_preservation.py` 모두 exit 0. 앱 코드 변경 없음. 상세: `validation/chat-worker-approved-knowledge.md`.

- 변경 모듈: `chat_data_analysis`, `chat_data_contracts`, `chat_workflow`, `chat_contracts`, `chat_skills`, `chat_history`, `evaluation_analysis`, `evaluation_recommendation`, `evaluation_results`, `knowledge_watch`, `activity`, `ChatDataAnalysisAnswer`, `ChatDrawer`, `chatDataAnalysis`, `App`의 챗 선택 전달.
- 후속 기능: 동일 분석 워커·취소/재시도, 확인 요청 답변·승인·분석 재개, 결과 지식 승인, 대화 저장·복원, 기존 사례/SQL/데이터셋/카드 제안, 프로젝트 경계·원천 검증.
- 연결 근거: `rg -n 'data_analysis|_chat_analysis|case_ref|_evaluation_case' backend/app frontend/src`.
- 핵심 수용 명령: `.venv\Scripts\python.exe -m pytest backend/tests/test_chat_data_analysis.py backend/tests/test_chat_history.py backend/tests/test_evaluation_recommendation.py -q`.
- 전파 명령과 실제 결과는 `validation/chat-worker-analysis.md`. 기존 편집·승인·분석 회귀 312개 및 추가 연결 검증 42개 통과(겹치는 테스트 포함). 작업 상태·취소/재시도·계산·승인 설명 후속 78개 통과. 화면 전체 231개, TypeScript/Vite 및 생성 계약 검사 통과.
- 로컬 현재 데이터: `validation/verify_chat_worker_local.py`는 모델 호출 없이 검증 계획을 사용하고 실제 앱 워커에서 243행의 상관·그룹·ML을 계산. 검증 대화 3개와 승인 대기 1개만 남김. 원래 메타데이터 32개 테이블 1,551행 보존은 `validation/verify_chat_worker_preservation.py`로 검증.
- 실제 모델 후속 검증: 사용자가 OpenRouter 전송 범위를 명시적으로 승인한 뒤 `z-ai/glm-5.3-flash`로 상관·그룹 통계·ML·일반 용어의 ML·모호한 장비 상태 질문을 검사했다. 현재 XXXA 243행의 계산·대화 복원·승인 대기 연결 및 모호한 연결의 확인 요청을 검증했다. 원시 측정 행은 모델에 보내지 않았다. 결과: `validation/chat-worker-live-validation.md`.
- 실모델에서 발견한 수정: 상관/ML 설정 혼용을 방법별 스키마로 차단, 원문 근거 인용 지침 보강, 분석 계획 입력을 승인된 범위의 사실로 한정. 미확인 질문은 추측 컬럼 계획과 분리하고 검증 오류 진단을 기록한다. 기존 기능 회귀 314개, 마지막 질문 계약/진단 변경 후 관련 검사 59개 통과(중복 합산하지 않음).
- 전파 규칙: 선택·범위·저장 응답·승인 근거 변경 시 위 후속 기능을 모두 재검증한다. 모델 실호출 미검증을 전체 검증 완료로 표시하지 않는다.

## 프로젝트 데이터 경계·DB 원본 구분 (2026-10-25)

- 변경 모듈: `dataset_scope`, `query`, `dashboard_period`, `source_discovery`, `dataset_reuse`, `evaluation_datasets`, `chat_tools`, `DataManager`, `DatasetEditor`, `DatasetScopeBadge`, `CardEditor`, `datasetInventory`.
- 후속 기능: SQL·간편 작성·JOIN/CTE/집계, 카드·간트·Python 입력, AI 데이터셋 재사용·분석 추천, 기존 공통 참조 카드 편집, 프로젝트 전환, 승인·분석 재개.
- 계약: 프로젝트 목록·AI 후보는 정확히 같은 범위만 제공. 프로젝트 실행은 원천 단계에서 범위를 제한. 공통 정의의 명시적 기존 카드 참조는 편집을 유지하되 조회는 선택한 프로젝트로 제한. 원본 표시에는 생성 식별자와 실제 정의를 함께 검증.
- 핵심 검증: `.venv\Scripts\python.exe -m pytest backend/tests/test_dataset_scope.py backend/tests/test_project_datasets.py backend/tests/test_dataset_reuse.py backend/tests/test_direct_sql_api.py backend/tests/test_query_contracts.py backend/tests/test_python_card_api.py backend/tests/test_chat_workflow.py backend/tests/test_period_form_execution.py -q`.
- 전파 검증: 아래 승인·데이터 연결·편집 검증과 `test_evaluation_datasets`, `test_evaluation_analysis`, `test_evaluation_natural_flow`, `test_knowledge_watch`, `test_gantt`, `test_dashboard_period`, `test_dataset_impact`.
- 화면: 전체 228개 테스트, TypeScript·Vite 빌드. 실제 앱에서 XXXA 목록·쿼리 관리·기존 카드 편집 확인.
- 실제 DB: `validation/verify_dataset_boundaries_live.py`; 보존 비교: `validation/verify_dataset_boundary_preservation.py`.
- 결과: `validation/dataset-boundaries.md`. 원천 데이터는 읽기 전용이며 기존 메타데이터 32개 테이블 1,464행의 값은 보존되었다.

이 작업 공간은 Git 기준 커밋 없이 운영되고 있습니다. 변경 직전 운영 데이터의 비교 기준은
`validation/narrative-chat-metadata-before.json`이며, 직전 실제 사례 흐름은
`validation/live-case-walkthrough.md`에 기록되어 있습니다.

## 승인 근거 설명

- 변경 모듈: `evaluation_narrative`, `evaluation_chat`, `chat_workflow`, `chat_contracts`, `chat_skills/evaluation_explanation.md`.
- 후속 기능: 대화 저장·재개, 기존 쿼리·데이터셋·카드 제안, 근거 표시와 분석 진입.
- 연결 근거: `rg -n 'evaluation_cases|evaluation_narrative|evaluation_explanation' backend/app frontend/src`.
- 수용 검증: `.venv\Scripts\python.exe -m pytest backend/tests/test_evaluation_narrative.py backend/tests/test_evaluation_chat.py backend/tests/test_evaluation_chat_workflow.py backend/tests/test_chat_workflow.py backend/tests/test_chat_history.py -q`.
- 전파 규칙: 출력/근거/복원 변경 시 후속 기능과 아래 승인·편집 검증을 함께 실행합니다.

## 승인·데이터 연결·편집

- 후속 기능: 승인 사례 검색, 결과 지식 축적, 확인 요청, 데이터셋 생성안, 프로젝트 및 카드 편집.
- 연결 근거: `rg -n 'retrieve_evaluation_cases|compact_evaluation_answer|restore_evaluation_answer|evaluationPreparation' backend/app frontend/src`.
- 수용 검증: `.venv\Scripts\python.exe -m pytest backend/tests/test_evaluation_cases.py backend/tests/test_evaluation_results.py backend/tests/test_evaluation_recommendation.py backend/tests/test_evaluation_datasets.py backend/tests/test_evaluation_natural_flow.py backend/tests/test_chat_knowledge.py backend/tests/test_chat_knowledge_apply.py backend/tests/test_project_datasets.py backend/tests/test_python_card_api.py -q`.
- 전파 규칙: 승인/범위 검증 변화 시 설명과 저장된 대화의 근거 취소 검증도 실행합니다.

## 화면

- 변경 모듈: `ChatEvaluationCases.tsx`, `evaluationCases.ts`, `chat-evaluation-cases.css`.
- 후속 기능: 채팅 메시지·이전 대화, 근거 분석 열기, 기존 분석/데이터셋/카드 제안 버튼.
- 연결 근거: `rg -n 'ChatEvaluationCases|evaluationSuggestions|evaluationNarrative' frontend/src`.
- 수용 검증: `node --experimental-strip-types --test frontend/tests/*.test.mjs`.
- 빌드: frontend에서 `node node_modules/typescript/bin/tsc -b`, 이후 `node node_modules/vite/bin/vite.js build`.
- 전파 규칙: 공개 응답 형태 변경 시 백엔드 계약 및 위 전체 화면 검증을 실행합니다.

공통 테스트는 분리된 fixture와 기존 읽기 전용 원천을 사용합니다. 실제 모델 검증은 원천 행 대신 현재 승인된 사례와 결과 요약만 사용합니다.
승인 근거 설명의 실행 결과는 `validation/narrative-chat.md`에 기록합니다.

## 질문의 프로젝트·공정·측정 항목 연결

- 변경 모듈: `chat_grounding`, `assistant`, `chat_workflow`, `analytics/chat_contracts`, `analytics/chat_tools`, `analytics/proposals`, `ChatDrawer.tsx`.
- 후속 기능: 현재 데이터 분석과 사례 검색 구분, 전역 대화의 명시적 프로젝트 지정, 조건안 저장·복원·확인, 기존 SQL·데이터셋·카드 요청.
- 연결 근거: `rg -n 'analysis_request|routing_loader|origin_project_id|reviewAnalysis' backend/app frontend/src`.
- 수용 검증: `.venv\Scripts\python.exe -m pytest backend/tests/test_chat_grounding.py backend/tests/test_analytics_chat.py backend/tests/test_analytics_proposals.py backend/tests/test_chat_history.py backend/tests/test_chat_workflow.py backend/tests/test_evaluation_chat_workflow.py -q`.
- 실제 모델/앱 검증: `validation/diagnose_grounding_route.py`, `validation/verify_chat_grounding_live.py`. 후자는 검증 대화와 미확인 조건안만 추가하며 분석 실행·지식 승인은 하지 않습니다.
- 전파 규칙: 범위 해석 변경 시 대화 복원, 분석 확인, 권한·원천 재검증을 함께 실행합니다. 분류 지침 변경 시 현재 관측/과거 사례/SQL/데이터셋/카드 요청을 실제 모델로 확인합니다.
- 결과 및 제한: `validation/chat-grounding.md`. 메타데이터 보존 기준은 이번 검증 직전의 `validation/chat-grounding-metadata-before.json`입니다.

## 관계 진술·데이터 연결·계산 근거 분리 (2026-10-25)

- 변경: semantic/schemas, collector_schemas, evaluation_extraction, evaluation_claims, collector_service, evaluation_terms, service, evaluation_results, evaluations, evaluation_narrative.
- 후속 연결: 자연어 등록 → 근거 구절 복원 → 용어 질문 → 승인 → 사례 검색 → 데이터셋/분석 추천 → 실행 결과 검토 → 챗 근거. 공개 응답/초안 복원/원본 수치 불변을 함께 검증한다.
- 연결 근거: `rg -n 'ExtractedKnowledge|for_kind|execution_evidence|evaluation_context|evidence_payload|facts_for' backend/app`; 화면은 `rg -n 'KnowledgeContent|interpretation|EvaluationKnowledgeSummary|validKnowledgeContent' frontend/src`.
- 수용: `.venv\Scripts\python.exe -X utf8 -m pytest backend/tests/test_evaluation_relationships.py backend/tests/test_evaluation_extraction.py backend/tests/test_evaluation_natural_flow.py backend/tests/test_evaluation_results.py backend/tests/test_semantic_collection.py -q`.
- 후속 회귀: 평가/챗/지속 분석/데이터셋/대시보드/카드 관련 전체 모듈을 `validation/relationship-knowledge.md`의 명령으로 실행한다.
- 화면: `node --experimental-strip-types --test frontend/tests/*.test.mjs`; frontend에서 `node node_modules/typescript/bin/tsc -b`, `node node_modules/vite/bin/vite.js build`.
- 실제 모델: `validation/verify_relationship_provider.py`는 합성 자연어와 합성 용어 정의만 전송한다. 측정 행을 읽거나 기존 승인 지식을 변경하지 않는다.
- 보존: 읽기 전용 메타데이터 행 해시를 전후 비교한다. 기존 평가 기록에 관계를 소급 추정하거나 지식 승인을 자동 수행하지 않는다.

## 평가 사례 전용 읽기·보완 화면 (2026-10-25)

- 변경: `frontend/src/SemanticEditor.tsx`, `SemanticWorkspace.tsx`. 기준: 직전 관계 지식 단계의 화면 234개·타입 검사·빌드 통과 상태.
- 연결 근거: `rg -n 'SemanticEditor|definitionDraft|evaluation_context' frontend/src -g '*.tsx' -g '*.ts'`. 직접 소비자는 SemanticWorkspace이며, 다른 지식 종류와 공유하는 폼 및 원문 확인 요청 이동이 영향 범위다.
- 화면 검증: `node --experimental-strip-types --test frontend/tests/*.test.mjs`; frontend에서 `node node_modules/typescript/bin/tsc -b`, `node node_modules/vite/bin/vite.js build`.
- 수동 수용: 평가 사례에서 업무 영역/업무 설명/동의어/공통 정의 편집 입력이 없어야 하며, 같은 항목의 원문·확인 요청과 버전 이력이 열려야 한다. 업무 용어의 새 지식에는 기존 공통 편집 항목이 유지되어야 한다.
- 저장 내용 검증: 읽기 전용 `/api/semantic/items` 응답의 content/version/status/binding 해시를 전후 비교한다. 평가 사례 화면에서 일반 정의 저장·직접 승인 경로는 표시하지 않고 기존 확인 요청으로 연결한다.
- 전파/제외: 서버 계약, 지식 구조, 분석·추천 코드와 프로젝트/데이터셋/카드 화면은 변경하지 않는다. 서버 계산 테스트는 이번 화면 변경으로 다시 실행하지 않는다. 별도 모델 호출이나 원시 데이터 전송도 없다.
- 결과: `validation/evaluation-view-cleanup.md`.
