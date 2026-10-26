# 사내 시연·작은 수정 인계

기준일: 2026-10-26. 개발용 인계 문서이며, 앱 안의 AI 지식은 별도의 DB에 저장된다.

## 먼저 이해할 제품

엔지니어는 프로젝트별 데이터를 살펴보고 데이터셋·대시보드 카드를 직접 또는 AI 채팅으로 만든다. AI 지식에 짧은 자연어 평가 사례를 입력하면 AI가 기존 승인 지식과 실제 데이터 구조를 참고해 정리한다. 모호한 연결은 확인 요청에서 질문하고, 검토·승인한 지식을 이후 분석과 답변에 사용한다.

현재 데이터 분석은 앱 내부 통계·ML 워커가 계산한다. 모델은 질문 해석·계획·설명을 담당하며 계산 결과를 임의로 만들지 않는다. 승인된 지식으로 분석을 추천하고, 설정된 지속 분석에서는 데이터 변경을 감지해 분석한다. 새 결과도 사용자 검토·승인을 거쳐 축적한다. 모든 PARA를 무조건 탐색하거나 관측 결과를 인과관계로 확정하는 제품이 아니다.

업무 관계의 출발점은 `wafer_his의 operation별 recipe/eqp_id → Inline의 operation+parameter → EPM parameter`다. 직접 영향·간접 영향·조건부 관계와 장비/시간/Lot 등의 혼동 요인이 있을 수 있다. `lot_cd`는 프로젝트 구분에 보존한다. Wafer 연결 키, 측정 단위, 시간 순서, 반복 측정 처리도 별도로 확인한다.

## 실행 구조와 코드 지도

```text
브라우저: React + TypeScript + MUI
    → FastAPI API / 채팅 스트림
        → 앱 관리 PostgreSQL: 프로젝트·데이터셋 정의·카드·지식·작업·대화
        → 활성 원천 PostgreSQL: 실제 측정값, 읽기 전용 조회
        → 사내 호환 LLM API: 해석·계획·설명
        → 프로세스 내부 워커: 지식 정리·분석·정기 확인
```

| 영역 | 처음 확인할 파일 |
| --- | --- |
| 앱 시작·API·워커 수명 | [backend/app/main.py](backend/app/main.py), [bootstrap.py](backend/app/bootstrap.py) |
| 환경·DB 연결·범위 | [db.py](backend/app/db.py), [database_settings.py](backend/app/database_settings.py), [dataset_scope.py](backend/app/dataset_scope.py) |
| 원천 발견·프로젝트 설정 | [source_discovery.py](backend/app/source_discovery.py), [project_settings.py](backend/app/project_settings.py) |
| SQL·데이터셋·카드 조회 | [query.py](backend/app/query.py), [schemas.py](backend/app/schemas.py) |
| 채팅 분류·제안·적용 | [assistant.py](backend/app/assistant.py), [chat_workflow.py](backend/app/chat_workflow.py), [chat_tools.py](backend/app/chat_tools.py) |
| 챗의 실제 통계·ML | [chat_data_analysis.py](backend/app/chat_data_analysis.py), [evaluation_analysis.py](backend/app/evaluation_analysis.py) |
| 사례 구조화·용어 연결·확인 요청 | [evaluation_extraction.py](backend/app/semantic/evaluation_extraction.py), [evaluation_terms.py](backend/app/semantic/evaluation_terms.py), [collector_service.py](backend/app/semantic/collector_service.py) |
| 결과 승인·지속 분석 | [evaluation_results.py](backend/app/semantic/evaluation_results.py), [knowledge_watch.py](backend/app/knowledge_watch.py) |
| FE 화면·상태·스타일 | [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) |
| 로컬 실행·반입 | [COMPANY_DEMO_GUIDE.md](COMPANY_DEMO_GUIDE.md), [scripts](scripts) |

파일 이름이나 과거 문서만으로 현재 메뉴를 추정하지 않는다. 제거된 백업·운영 화면 등의 코드/계약 일부는 남아 있다. 현재 진입점과 실제 API 노출, 사용자 요구를 확인한다. 이전 설계 HTML과 초기 문서에는 현재와 다른 화면·흐름이 있으므로 그대로 복구하지 않는다.

## 회사 PC에서 달라지는 것

| 항목 | 실제 확인 사항 |
| --- | --- |
| 설치 | Windows, Python, Node/pnpm, 로컬 PostgreSQL 실행 허용, 사내 패키지/파일 전달 방식 |
| 데이터 | `.local/imports/export_01/{wafer_his,inline,epm}/`의 모든 Parquet part 파일. 실제 컬럼명·타입과 `lot_cd` 보존 |
| 프로젝트 | 프로젝트 관리에서 실제 `lot_cd` 값 연결. 합성 XXXA/XXXB 값이 회사와 같다고 가정하지 않음 |
| 모델 공통 설정 | `LLM_BASE_URL`, `LLM_MODEL`. 현재 앱은 `/chat/completions` 호환 응답을 검증함 |
| 개인 챗 키 | 앱 프로필의 LLM API 키 설정. [llm_settings.py](backend/app/llm_settings.py) |
| 백그라운드 지식 정리 키 | 서버의 `KNOWLEDGE_LLM_API_KEY`. [collector_config.py](backend/app/semantic/collector_config.py). 개인 챗이 된다고 자동 정리 키도 준비된 것은 아님 |
| 인증 | 1차 로컬 시연은 local_admin. 실제 SSO 함수는 아직 미연결이며 [sso_adapter.py](backend/app/sso_adapter.py)에 회사 계약을 연결해야 함 |
| 저장 설정 | DB/LLM 자격증명 암호화 키 유지. Git으로 코드를 옮겨도 카드·지식·키·데이터는 자동 이전되지 않음 |

회사 모델에 붙이는 요청 형식·인증 헤더 수정은 실제 계약을 확인한 뒤 좁게 수정한다. DB/원시 행을 모두 모델에 넣는 방식으로 질문 해석 오류를 해결하지 않는다. 사내 Claude Code의 모델과 웹 앱이 호출하는 LLM은 별도 연결이다.

## 작은 수정의 진단 순서

| 증상 | 먼저 확인할 것 |
| --- | --- |
| 앱이 열리지 않음 | 관리 스크립트의 실행 상태, 8000 포트, `/api/health`, 비밀값을 가린 오류 |
| 차트가 비어 있음 | 활성 DB, 프로젝트 `lot_cd`, 조회 시작 주차, 카드 필터·데이터셋 버전 |
| 새 DB에 카드·지식이 없음 | DB별 저장 범위와 이전 여부. DB 호스트·포트·이름이 식별에 쓰임 |
| 회사 컬럼을 이해하지 못함 | 실제 스키마, 승인된 용어/매핑, 연결 키·단위·시간 조건, 확인 요청 |
| 채팅은 되는데 자동 정리 실패 | 공통 endpoint/model과 서버의 지식 정리 키, 응답 검증 오류 |
| 통계·ML에서 데모 컬럼을 찾음 | `chat_workflow.py`의 범용 분석 분기. 예전 PARA 도구는 고정 스키마 의존성이 남아 있음 |
| 화면 저장이 안 되거나 이전 값 표시 | API 오류·버전 충돌, workspace token, query cache, 로컬 초안. 검증을 제거하지 않음 |
| UI 수정이 보이지 않음 | `frontend/dist` 재빌드 여부. `start-app.ps1 -NoBuild`는 소스를 빌드하지 않음 |

회사 컬럼에 대한 사용자 설명은 확인 요청을 통해 승인 지식으로 남긴다. 개발용 문서에 회사 데이터 의미를 하드코딩해 앱의 승인 체계를 대체하지 않는다.

## 검사 방법

2026-10-26 이전 작업의 검증 기준은 백엔드 1,402개·FE 234개 통과, TypeScript/Vite 성공이다. 합성 반입 DB에서 프로젝트 분리·카드·실제 통계/ML·질문/승인·지식 재사용을 확인했다. 이 수치는 현재 회사 PC나 이번 수정의 결과가 아니다. [실제 검증 범위](validation/company-demo-readiness.md)를 읽는다.

백엔드 검사에는 별도 메타데이터 DB와 합성 원천이 필요하다. 현재 `.env`의 회사/사용자 DB로 `pytest`를 바로 실행하지 않는다. 제공하는 `validation/company_demo_qa.py`는 이 저장소가 준비한 로컬 DB 환경 전용이며, `.local/runtime.json`, 초기화된 `wafer_app`, 합성 fixture가 준비된 `wafer_demo`가 필요하다. 없는 fixture를 만들려고 회사 DB를 초기화하지 않는다. 먼저 [시연 지침](COMPANY_DEMO_GUIDE.md)의 합성 환경 절차를 확인한다.

아래 명령은 위 조건을 확인한 후 프로젝트 루트에서 사용한다.

```powershell
# 최초 1회만. 상태 파일이 있으면 재생성하지 말고 검증 DB 구성을 확인한다.
.\.venv\Scripts\python.exe -X utf8 validation/company_demo_qa.py prepare

# 변경한 흐름의 관련 검사부터 실행한다. 아래는 챗 통계·ML 경로의 예다.
.\.venv\Scripts\python.exe -X utf8 validation/company_demo_qa.py run -m pytest backend/tests/test_chat_data_analysis.py backend/tests/test_assistant_routing_context.py -q

# 범위가 넓은 변경에는 전체 검사
.\.venv\Scripts\python.exe -X utf8 validation/company_demo_qa.py run -m pytest backend/tests -q
```

`.local/company-demo-qa.json`과 키는 해당 PC에서 생성한 검증 상태이며 Git에 넣지 않는다. `prepare` 시점의 사용자 DB 해시와 비교하는 `preserve`는 그 사이 실제 사용자 작업이 없을 때만 불변 검증으로 해석한다. 합성 검증의 모델 응답은 대체 응답일 수 있으므로 실제 회사 모델 검증과 구분한다.

FE 검사와 실제 화면 확인은 [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)를 따른다. 기존 [REGRESSION.md](REGRESSION.md)에서 영향 받는 연결 흐름을 고르고, 재현 조건·원인·변경 파일·실행 명령/결과·남은 환경 제약을 기록한다.

## Claude 또는 다른 사내 LLM에 전달하는 방법

Claude Code는 프로젝트 폴더에서 시작하고 `CLAUDE.md`와 가져온 `AGENTS.md`가 읽혔는지 확인한다. 일반 사내 LLM 채팅은 파일 자동 로드를 기대하지 말고 공통 지침·이 문서·관련 코드만 제공한다. FE 작업이면 FE 지침도 함께 제공한다. 코드 수정·실행 도구가 없는 채팅은 실제 검증을 수행했다고 말할 수 없다.

최초 요청 예시:

> CLAUDE.md, AGENTS.md, COMPANY_HANDOFF.md, COMPANY_DEMO_GUIDE.md를 읽고 이 프로젝트의 로컬 시연을 준비해줘. FE를 수정할 때는 FRONTEND_GUIDE.md도 읽어줘. 현재 목표는 Windows PC에서 1차 시연이며, 기존 프로젝트·데이터셋·카드·채팅·질문·승인 기능과 lot_cd를 유지해줘. 환경 차이로 생긴 문제는 재현 후 필요한 부분만 수정하고, 변경 이유와 실제 실행한 검사를 기록해줘. 기존 데이터와 비밀값을 보존하고, 미검증 항목은 구분해서 알려줘.

추가 수정은 “화면/기능, 기대 동작, 실제 동작, 재현 순서”를 함께 전달한다. 수정 때마다 확인된 환경 차이와 해결책을 기록해 다음 세션도 같은 사실에서 시작하도록 한다.

Claude 지침 파일과 가져오기 동작의 참고: [Claude Code 공식 문서](https://code.claude.com/docs/en/memory). 지침 파일은 모델의 작업 맥락이며 강제 검증 장치를 대신하지 않는다.
