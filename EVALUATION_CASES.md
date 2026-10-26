# AI 지식의 평가 사례와 채팅 분석 제안

기존 AI 지식에 사내 평가 결과를 자연어로 등록하고, 현재 데이터와 관련된 승인 사례를 기존 AI 채팅에서 찾아 추가 비교를 제안한다. 제안을 현재 데이터셋에서 실행한 뒤, 계산 근거를 검토·승인하여 다시 AI 지식에 축적할 수 있다. 그룹 차이와 상관관계는 관측 통계이며 원인을 자동 확정하지 않는다.

## 사용 흐름

1. **AI 지식 → 평가 사례 → 평가 사례 등록**에서 **적용 범위(공통/프로젝트)와 자연어 평가 내용**만 입력한다. 데이터셋을 미리 만들거나 선택할 필요가 없다. 공정·Recipe 변경, Inline/EPM 관측 결과, 제품·장비·기간을 아는 범위에서 적는다.
2. AI가 원문을 정리하고 기존 승인 지식으로 용어를 연결한다. 같은 용어에 여러 뜻이 있거나 데이터 연결이 없으면 **확인 요청**의 같은 항목에서 짧게 질문한다. 사용자는 자연어로 답하거나 모르는 부분이라고 알려줄 수 있다. 처리 실패 시 원문을 유지하고 기존 재시도로 다시 구조화한다.
3. **확인 요청 → AI가 이해한 내용**에서 보고 내용과 용어 해석을 읽고 승인한다. 원문·답변 이력·상세 연결 근거는 접혀 있으며, 잘못 이해한 부분은 자연어로 보완한다. 일반 사용자의 등록·답변과 기존 관리자 승인 권한을 유지한다. 아직 실제 데이터에 연결하지 못한 보고도 미확인 상태를 명시해 보관할 수 있다.
4. 프로젝트의 데이터셋 또는 카드를 선택한 뒤 AI 채팅에 **현재 데이터와 관련된 평가 사례를 찾아 추가 분석을 제안해줘**라고 요청한다.
5. 보고된 과거 관측, 적용 조건, 관련 이유와 추가 비교 제안을 확인한다. 제안 버튼은 질문 입력란을 채운다. 사용자가 전송하면 기존 SQL·데이터셋·카드 제안 경로로 진행하고, 미리보기·적용은 기존 절차를 따른다.
6. 제안의 **현재 데이터로 분석**을 누르면 선택 데이터셋의 이름·버전·적용 필터·기간을 확인한다. **Recipe·장비 등 그룹별 차이** 또는 **두 측정 항목이 함께 변하는 관계**를 선택하고 실제 컬럼을 지정한다. 실행을 누른 후에 계산하며 채팅을 닫아도 진행된다.
7. 그룹별 유효/결측 수·평균·표준편차·최소/최대, 지정한 두 그룹의 평균 차이·변화율, 또는 같은 행의 두 수치에 대한 Pearson 상관계수를 확인한다. 재시도·취소는 기존 작업 제어와 연결된다.
8. **AI 지식 검토 요청**으로 실행 근거와 메모를 **확인 요청**에 보낸다. 관리자가 승인해야 사례 검색에 포함된다. 반려 후 보완 답변은 검토 메모로 남고 원래 계산 수치는 유지된다. **사례 분석으로 돌아가기**로 최신 검토 상태를 확인할 수 있다.

기존 프로젝트, 대시보드, 데이터셋·쿼리·카드 편집, AI 지식의 다른 종류와 채팅 요청 예시는 유지한다.

상단의 **원인 및 영향 분석**, **분석 기록** 독립 메뉴는 제거했다. 새 흐름은 채팅과 AI 지식에서 이용한다. 기존 계산기·실행 기록·질문 승인 후 복귀·재시도·변화 알림 연결은 계속 필요하므로 내부 기능과 저장 데이터는 유지하며, 채팅의 **이전 분석 결과**에서 기존 기록을 열 수 있다.

## 근거와 범위

- 평가 사례는 `evaluation` 종류로 기존 지식 테이블에 저장한다. 새 DB 테이블이나 기존 데이터 변환은 없다.
- 공정, Recipe 변경, Inline 관측, EPM 관측, 평가 조건, 보고된 근거, 한계를 원문 인용과 함께 구조화한다. 새 등록은 원문과 적용 범위를 보존하고, 예전 등록의 원문·데이터셋 스냅샷·ID·승인 이력도 그대로 유지한다.
- 구조화된 사실에 원문 근거가 없으면 승인 전에 확인하도록 한다. 조건이 기록되지 않은 보고도 그 한계를 명시하여 활용할 수 있다.
- 채팅 검색은 현재 DB, 프로젝트 또는 공통 데이터셋, 선택 데이터셋·카드, 질문의 공정/PARA 정보로 범위를 정한다. 같은 원천을 공유한다는 이유만으로 평가 조건까지 같다고 해석하지 않는다.
- 미승인·폐기·접근 불가 사례는 검색에서 제외한다. 예전 데이터셋 기반 사례에는 기존 스냅샷 유효성 검사를 유지한다. 새 범위 기반 사례는 데이터셋 변경만으로 원문을 무효화하지 않으며, 연결된 지식·데이터 연결의 버전과 상태를 별도로 재확인한다. 같은 원천·범위·원문을 중복 등록하면 기존 확인 요청을 안내한다.
- 새 사례의 승인된 용어 별칭은 동일 프로젝트 또는 허용된 공통 범위에서 재사용한다. 기존 승인 정의가 없는 설명은 실제 컬럼·값 연결이 완료된 것처럼 취급하지 않는다. 승인 여부와 데이터 연결 여부를 구분한다.
- 기존 의미 지식 검색과 사례 검색을 분리하여, 사례의 이름이나 미정 조건 때문에 기존 SQL·카드 작성이 차단되지 않도록 한다.
- 채팅 기록에는 사례 식별자·버전·검색 범위를 저장한다. 대화를 다시 열 때 승인과 원천 상태를 재확인하며, 다른 사례나 최신 버전으로 조용히 바꾸지 않는다.

등록·승인은 해당 보고 또는 계산 결과의 검토를 뜻한다. 다른 데이터 범위에서 재현됐다는 의미나 인과관계의 확정을 뜻하지 않는다. 전 PARA 자동 ML 탐색은 포함하지 않는다.

## 현재 데이터 실행의 범위

- 기존 데이터셋의 SQL·피벗·저장 조건을 재사용하고, 선택 카드 필터와 적용 기간을 함께 전달한다. 사용하지 못하는 조건, 바뀐 데이터셋/원천/프로젝트 범위는 조용히 무시하지 않고 새 확인을 요청한다.
- 수치 집계는 읽기 전용 DB의 한 일관된 조회 시점에서 계산한다. 원시 측정 행 전체를 LLM에 넘기지 않는다. 계산 결과에는 실행 ID, 데이터셋 버전, 조건, 조회 시각, 계산 방법을 보존한다.
- 상관분석은 같은 데이터셋 행의 두 수치만 대응한다. 서로 다른 Inline/EPM 테이블의 wafer·측정 시점을 임의로 연결하지 않는다. 대응 데이터가 없다면 기존 채팅의 데이터셋 작성 기능으로 먼저 구성한다.
- 여러 PARA 또는 공정의 측정값을 혼합한 집계는 차단한다. 원천 측정의 `value`만 남기고 PARA 식별을 숨긴 데이터셋도 그대로 계산하지 않는다. 한 계열로 범위를 좁히거나 의미가 명확한 수치 컬럼으로 구성해야 한다.
- 실행당 최대 200,000행·30개 그룹을 지원한다. 한도를 넘으면 일부만 계산하지 않고 범위를 좁히도록 안내한다. 유효 수치가 없거나 상관계수를 계산할 수 없는 결과는 지식 등록을 차단한다.
- 실행은 기존 `AnalysisRun`에 `kind=evaluation`로 저장하며 작업자·상태·재시도·취소를 재사용한다. 지속 분석의 설정만 별도 테이블에 저장하며 기존 데이터는 변환하지 않는다.
- 검토 초안의 `analysis_result`는 완료된 실행의 서버 계산 요약에서만 생성한다. 수치·조건·참조를 임의로 바꾸면 승인되지 않는다. 일반 자연어 평가 원문이나 LLM 응답으로 계산 결과를 위조할 수 없다.
- 새 API는 `/api/evaluation-analysis/prepare`, `/{id}`, `/{id}/execute`, `/{id}/submit-review`다. 기존 지식 승인 API를 그대로 사용한다.

## 승인 지식 기반 지속 분석 — 2026-10-25

AI 지식 → 평가 사례 → **지식을 바탕으로 새 데이터 지속 분석**에서 시작한다. 승인 사례와 기존 데이터셋을 선택하면 AI가 최초 조건을 추천한다. **이 추천으로 분석 시작**을 누르면 첫 분석과 이후 지속 분석을 함께 시작한다. 지속 분석 체크를 해제하면 한 번만 실행한다. 채팅에서 준비한 평가 분석에서도 동일한 추천을 사용할 수 있다.

1. 처음에는 관련 사례와 대상 데이터셋을 선택한다. AI가 승인 지식과 실제 출력 컬럼을 근거로 목표·비교 항목·표본·시점 및 통계/ML 방법을 추천한다. 모호한 연결은 확인 요청에서 자연어로 질문하고, 답변이 검토·승인된 후 추천으로 돌아오면 다시 정리한다. Recipe·장비·Inline·EPM을 함께 보려면 기존 데이터셋 기능 또는 AI 채팅으로 wafer/Lot·공정·시점이 맞게 대응된 행을 먼저 구성해야 한다. 자동으로 모든 PARA를 결합하거나 인과 경로를 추정하지 않는다.
2. 기본 1시간마다 선택 범위의 내용 지문을 확인한다. 추가뿐 아니라 뒤늦은 수정·삭제도 감지한다. 동일하면 계산을 생략하고, 변경되면 같은 조건으로 기존 작업자에 재분석을 요청한다. 실시간 CDC나 증분 모델 학습이 아니라 주기적 조회와 범위 재계산이다.
3. 그룹 비교·상관 통계 외에 **여러 요인과 결과의 관계 · 통계와 ML**을 선택할 수 있다. 목표 수치 1개, 설명 항목 1–8개, Lot/wafer 등 표본 식별자와 시각 컬럼을 지정한다. 통계와 Random Forest 회귀를 로컬 작업자에서 계산하며 원시 행 전체를 LLM으로 전송하지 않는다.
4. 시간순으로 과거 학습/이후 검증을 나누고 검증 표본과 같은 Lot/wafer는 학습에서 제외한다. 미래 검증 오차를 학습 중앙값 기준과 비교한다. 기준보다 나은 경우에만 permutation importance를 보여준다. 표본 부족이나 예측력 부족을 관계 발견으로 표현하지 않는다. 중요도는 인과 효과 또는 영향 비율이 아니다.
5. 완료 결과는 동일한 실행 근거를 보존한 채 **확인 요청**에 자동 등록한다. 사용자가 검토·승인해야 AI 지식 및 기존 채팅의 관련 사례 검색에 반영된다. 계산 수치·실행 참조를 답변으로 바꿔 승인할 수 없다. 기존 질문·보완·반려·승인·재시도·취소 경로를 재사용한다.

앱 서버가 실행 중이며 해당 DB가 선택되어 있어야 한다. 데이터셋 버전·원천 범위·참고한 승인 지식·용어 연결·분석 방법 버전이 바뀌면 멈추고 재확인을 안내한다. 실행 실패/취소도 자동 반복하지 않고 일시정지하며, 기존 재시도 결과와 연결한다. 진행 중 작업 취소와 이후 확인 일시정지는 별도 동작이다.

일반 통계/내용 확인은 최대 200,000행, ML은 최대 20,000행이다. ML은 유효 표본 60행 이상과 시간/표본 분할 조건을 충족해야 하며, 범주형 설명 항목은 30개 값까지다. DB당 지속 분석 30개, 미처리 확인 요청 100개 및 기존 작업 한도를 적용한다. 한도 도달 시 무한 큐 생성이나 임의 표본 추출을 하지 않는다. 실행별 방법·라이브러리 버전, 난수 기준, 데이터셋 버전, 조회 시각, 조건과 관측 수를 보존한다.

`app_knowledge_watches`에 설정·진행 상태만 추가한다. 지식·데이터셋·카드의 기존 테이블 내용은 변경하지 않는다. 별도 서비스나 백업 기능을 추가하지 않는다. 되돌릴 때는 지속 분석을 일시정지하고 이전 앱을 실행하면 된다. 새 테이블과 기존 기록은 삭제할 필요가 없다.

설계 참고: [scikit-learn RandomForestRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html), [시간 순서와 그룹을 고려한 검증](https://scikit-learn.org/stable/modules/cross_validation.html), [Permutation importance의 해석 한계](https://scikit-learn.org/stable/modules/permutation_importance.html). 시간·그룹을 동시에 분리하는 로직은 이 앱에서 구현했다.

## 최초 조건 추천의 근거와 복구

- 추천에는 항목별 역할·선택 이유·승인 지식의 인용 근거와 데이터 대응 근거를 보존한다. 아직 조회하지 않은 관측값, 임의의 Recipe 그룹 값·필터·조인을 만들지 않는다. 실제 컬럼/타입과 인용을 서버에서 검증한다. 추천은 분석 결과 또는 승인 지식의 자동 확정이 아니다.
- 데이터 연결 질문은 기존 확인 요청의 자연어 답변·초안·검토·보완·승인을 재사용한다. 추가적인 컬럼 설정 폼 없이 ‘AI가 정리한 연결 설명’을 검토한다. 질문의 ‘분석 추천으로 돌아가기’로 복귀하면 승인 답변을 반영해 추천한다. 승인 전 답변이나 반려·모름 답변으로 실행하지 않는다.
- 연결 설명은 동일한 사례 버전·데이터셋 정의·프로젝트에 한해 다음 추천에서도 재사용한다. 일반 SQL/카드 작성의 공통 업무 정의로 자동 승격하지 않는다. 실제 계산 결과의 승인 사례는 기존 채팅 검색에 계속 제공한다.
- 추천 준비는 기존 분석 기록의 JSON에 보존한다. 화면을 닫은 뒤 ‘확인 중인 분석 추천’에서 이어갈 수 있다. 중단된 모델 요청은 5분 후 다시 요청할 수 있으며 오래된 응답은 새 요청을 덮어쓰지 않는다. 추천 요청은 사용자별 시간당 20회까지다.
- 실행 직전/작업자/지속 분석/재시도에서 원천·데이터셋·프로젝트·사용자 역할·사례 및 추천에 사용한 승인 설명을 다시 검증한다. 근거 변경 시 새 추천이 필요하다. 최초 실행과 지속 분석 설정은 한 트랜잭션으로 처리해 중복 클릭이나 설정 한도 초과로 부분 실행되지 않는다.
- 서버의 기존 공용 AI 연결을 사용한다. 원시 측정 행은 AI에 보내지 않으며 승인 지식과 선택 데이터셋의 정의만 전달한다. AI가 데이터셋을 새로 작성하거나 여러 후보에서 무조건 하나를 선택하는 기능은 아니다.

## 기존 연결 설정

자연어 구조화는 기존 지식 수집기의 공용 LLM 설정을 사용한다. 채팅의 요청 분류는 기존 개인 채팅 연결을 사용한다. 새로운 키나 외부 서비스를 추가하지 않는다.

## 확인 경로

- API: `POST /api/semantic/collection/evaluations`의 새 입력은 `{project_id: null | 기존 프로젝트 ID, text}`다. 기존 `{dataset_id, text}` 입력과 `GET /api/semantic/collection/evaluation-datasets`도 호환용으로 유지한다.
- 기존 질문·답변·초안·승인 API를 재사용한다. 채팅은 `evaluation_cases` 응답을 추가하며 기존 응답 형식을 유지한다.
- 신규 서버 검증: `backend/tests/test_evaluation_cases.py`, `test_evaluation_chat.py`, `test_evaluation_chat_workflow.py`.
- 신규 화면 검증: `frontend/tests/evaluationKnowledge.test.mjs`, `evaluationCases.test.mjs`.
- 브라우저 통합 검증: `validation/serve_evaluation_qa.py`, `validation/verify_evaluation_ui.cjs`. 임시 관리 DB와 고정 모델 응답을 사용하고 실제 등록·승인·검색·채팅 경로를 실행한다.
- 실제 기존 데이터 보존: `validation/verify_evaluation_preservation.py`로 변경 전후 테이블별 전체 행 지문을 비교한다.

## 첫 등록·추천 단계의 검증 결과 — 2026-10-24

변경 영향은 지식 종류·수집기·질문/승인에서 사례 검색, 채팅 응답·기록 복원까지 이어진다. 기존 의미 지식 검색과 SQL·데이터셋·카드 생성 경로는 별도로 유지하고 함께 회귀 검사했다. `change-impact`, `module-regression` 기준으로 이 연결 경로를 확인했다.

| 검사 | 결과 |
| --- | --- |
| 서버 전체 검사 | 1,174개 통과, 종료 코드 0. 최종 검색·초안 보완 전 전체 실행 결과이며 로그는 `.local/evaluation-backend-full.log`. |
| 최종 변경 후 관련 서버 회귀 검사 | 125개 통과, 종료 코드 0. 평가 사례·기존 지식·질문/승인·채팅·기록·분석 연결을 포함. `.local/evaluation-backend-final.log`. |
| 화면 전체 검사 | 202개 통과, 종료 코드 0. `.local/evaluation-frontend-tests.log`. |
| TypeScript 및 배포 빌드 | 모두 종료 코드 0. 기존 Plotly 묶음 크기 경고만 있음. |
| 브라우저 통합 검사 | 자연어 등록→추가 답변→승인→사례 검색→제안 선택→채팅 기록 복원 통과. 기존 프로젝트·데이터셋·쿼리·카드 편집도 확인. 브라우저 오류 없음. `validation/evaluation-ui.json`. |
| 실제 설정된 AI 응답 | 합성 평가 사례를 실제 연결로 구조화한 응답에서 원문 근거와 공정·Inline·EPM 추출을 확인. 미기재 조건·수치를 만들지 않음. 저장한 응답의 최종 재검증도 통과. `validation/evaluation-provider.json`. |
| 기존 데이터 보존 | 변경 전후 30개 테이블, 1,206행 전체 지문 동일. `validation/evaluation-metadata-before.json`, `evaluation-metadata-after.json`. |
| 실제 앱 반영 | `http://127.0.0.1:8000/` 정상 응답, 메타데이터/원천 연결 정상, 새 배포 화면과 평가 데이터셋 API 확인. |

주요 실행 명령은 다음과 같다. 모든 등록·승인 시험은 별도 임시 DB에서 수행했으며 실제 앱에는 시험 사례를 추가하지 않았다.

```powershell
.\.venv\Scripts\python.exe validation\run_midterm_audit_tests.py
.\.venv\Scripts\python.exe validation\run_midterm_audit_tests.py backend/tests/test_evaluation_cases.py backend/tests/test_evaluation_chat.py backend/tests/test_evaluation_chat_workflow.py backend/tests/test_semantic_collection.py backend/tests/test_semantic.py backend/tests/test_chat_workflow.py backend/tests/test_chat_history.py backend/tests/test_analytics_chat.py
node --experimental-strip-types --test frontend/tests/*.test.mjs
# 다음 두 명령은 frontend 폴더에서 실행
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build --outDir dist-evaluation
# 다음 명령은 프로젝트 루트에서 실행
.\.venv\Scripts\python.exe validation\verify_evaluation_provider.py --replay
.\.venv\Scripts\python.exe validation\verify_evaluation_preservation.py after
```

브라우저 통합 검사는 고정 모델 응답으로 연결과 권한 동작을 확인했고, 실제 AI 연결은 별도의 합성 사례로 확인했다. 여러 실제 평가 보고서에서의 추출 정확도나 분석 추천 품질을 통계적으로 평가한 결과는 아니다.

## 추가 분석 실행·승인 단계의 검증 — 2026-10-24

변경 연결은 `ChatEvaluationCases → EvaluationAnalysisPanel → evaluation_analysis → 기존 분석 작업자/작업 제어 → evaluation_results → 기존 질문/초안/승인 → 사례 검색/채팅 기록`이다. `App`의 주 메뉴를 정리하면서 과거 결과와 기존 분석 조건 확인은 보조 화면으로 연결했다. 기존 프로젝트·데이터 관리·쿼리 관리·카드 편집 구현은 변경하지 않았다.

- 서버 전체: **1,203개 통과**, 종료 코드 0 (`.local/evaluation-execution-backend-full.log`). 전체 실행 이후 보완한 항목은 아래 마지막 회귀 검사로 확인했다.
- 최종 관련 서버 회귀: **155개 통과**, 종료 코드 0 (`.local/evaluation-execution-final.log`). DB 전환 후 완료 결과 유지, 실행 중 범위 변경 차단, 임의 추출 쿼리 거절, 계산/검토/채팅/기존 질문 흐름을 포함한다.
- 화면 전체: **212개 통과**, 종료 코드 0 (`.local/evaluation-execution-frontend-tests.log`). TypeScript 검사와 배포 빌드도 종료 코드 0이며 기존 Plotly 묶음 크기 경고가 있다.
- 새 계산 검사는 읽기 전용 PostgreSQL에서 실제 SQL을 실행하여 그룹 평균·분산·차이, Pearson, NULL/비유한 값, 행/그룹 한도 및 혼합 PARA 방지를 확인한다.
- 승인 수치 위조, 일반 자연어 사례를 계산 결과로 둔갑시키기, 반려 후 답변으로 수치 변경, 데이터셋 변경 후 기존 결과 승인/재검색을 회귀 검사로 차단한다.
- 실제 앱의 기존 데이터 보존은 `validation/verify_evaluation_preservation.py before|after evaluation-execution`으로 별도 기준을 두고 비교한다.

마지막 관련 서버 검사 명령:

```powershell
$env:PM_AUDIT_LOG_NAME='evaluation-execution-final.log'
.\.venv\Scripts\python.exe validation\run_midterm_audit_tests.py backend/tests/test_evaluation_analysis.py backend/tests/test_evaluation_compute.py backend/tests/test_evaluation_results.py backend/tests/test_evaluation_cases.py backend/tests/test_evaluation_chat.py backend/tests/test_evaluation_chat_workflow.py backend/tests/test_analytics_worker.py backend/tests/test_chat_history.py backend/tests/test_semantic_collection.py backend/tests/test_semantic.py backend/tests/test_chat_workflow.py backend/tests/test_analytics_chat.py
```

새 브라우저 통합 검사는 `validation/verify_evaluation_execution_ui.cjs`를 사용한다. `serve_evaluation_qa.py`가 생성한 임시 관리 DB와 실제 합성 측정 원천을 사용하고, 별도 SQL로 계산한 그룹별 기대값과 화면 결과를 대조한다. 원본 측정값이나 실제 앱의 지식을 수정하지 않는다.

최종 브라우저 검증은 **모두 통과**했다 (`validation/evaluation-execution-ui.json`). 488개 wafer 대응 행의 두 그룹 평균·유효 수가 별도 SQL과 일치했고, 차이와 Pearson 계산, 결과 검토·승인, 승인 직후 자동 상태 갱신, 저장 채팅에서 결과 복원, 재시도 자식 실행 열기, 승인 결과의 관련 사례 재검색·원본 실행 열기를 확인했다. 브라우저 오류는 없었다. 승인 직후 30초 캐시가 남던 문제는 관련 조회 무효화와 재진입 조회로 수정했다.

실제 앱을 갱신한 후 새 분석 API·배포 화면·원천 연결이 정상임을 확인했다. 기존 관리 데이터 **30개 테이블·1,206행의 지문이 모두 동일**하다 (`validation/evaluation-execution-metadata-before.json`, `evaluation-execution-metadata-after.json`). 시험용 앱은 종료하고 해당 임시 관리 DB를 정리했다. 실제 앱에는 시험 사례나 실행을 추가하지 않았다.

현재 화면 빌드: `index-amh4-vGx.js`. 검증 화면: `.local/evaluation-execution-result.png`, `.local/evaluation-execution-correlation.png`. 현재 실제 앱의 메뉴 정리 확인 화면: `.local/evaluation-execution-live.png`.
