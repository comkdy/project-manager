# FE 설계·디자인·수정 지침

2026-10-26 기준. 목적은 현재 UI와 연결 흐름을 유지하며 사내 환경의 작은 문제를 고치는 것이다. 새 디자인 시안이나 전면 개편 명세가 아니다. 먼저 [AGENTS.md](AGENTS.md)를 읽고 실제 화면과 현재 코드를 기준으로 수정한다.

## 유지할 화면과 사용 흐름

- 상단 **프로젝트 / AI 지식**, 프로젝트 안의 **대시보드 / 데이터 관리**, 기존 AI 채팅 진입을 유지한다. 데이터셋 생성·쿼리 관리·카드 편집은 일반 사용자의 핵심 기능이다.
- 평가 사례는 **적용 범위 + 자연어**로 등록한다. 업무 영역·동의어·복잡한 매핑 입력 양식으로 되돌리지 않는다. 다른 지식 종류가 사용하는 기존 편집 필드는 별개다.
- **확인 요청**은 무엇을 답하거나 승인할지 먼저 보여 준다. 같은 항목의 자연어 답변→구조화→검토·승인을 유지한다. 구조화 세부 정보·근거는 필요할 때 펼쳐 보고, 작은 정보마다 박스를 만들지 않는다.
- 공통/프로젝트 전용, DB 원본/추가 데이터셋, 현재 조회 조건과 근거를 구분한다. 색상만으로 상태나 범위를 전달하지 않는다.
- 승인된 결과도 인과관계가 증명되었다는 뜻은 아니다. 관측 결과·AI 해석·추가 확인 제안을 문구에서 구분한다.
- 예전 설계 HTML과 남은 컴포넌트 파일만 보고 제거된 분석·기록·백업·운영 메뉴를 추가하지 않는다.

## 기존 구조에서 수정할 위치

| 관심사 | 기준 파일과 역할 |
| --- | --- |
| 전역 테마·Provider | [main.tsx](frontend/src/main.tsx): MUI 테마, CssBaseline, QueryClient, StrictMode |
| 전체 화면 전환·연결 | [App.tsx](frontend/src/App.tsx): 사용자/DB/프로젝트, 선택 데이터셋·카드, 채팅 제안, 질문·승인·분석 복귀 |
| 공통 디자인 | [styles.css](frontend/src/styles.css), [workspace-navigation.css](frontend/src/workspace-navigation.css) |
| 공통 피드백·표·필터 | [components.tsx](frontend/src/components.tsx): Loading, Empty, ErrorNotice, DataGrid, FilterBuilder |
| 대시보드·격자·기간 | [Dashboard.tsx](frontend/src/Dashboard.tsx), [dashboardGrid.ts](frontend/src/dashboardGrid.ts), [dashboardPeriod.ts](frontend/src/dashboardPeriod.ts), [dashboardRefresh.ts](frontend/src/dashboardRefresh.ts) |
| 데이터·SQL·피벗 | [DataManager.tsx](frontend/src/DataManager.tsx), [DatasetEditor.tsx](frontend/src/DatasetEditor.tsx), [useSimpleEditor.ts](frontend/src/useSimpleEditor.ts), [PivotEditor.tsx](frontend/src/PivotEditor.tsx) |
| 카드 편집·저장 | [CardEditor.tsx](frontend/src/CardEditor.tsx), [PythonCardEditor.tsx](frontend/src/PythonCardEditor.tsx), [cardDraftSave.ts](frontend/src/cardDraftSave.ts) |
| 채팅·제안 검토 | [ChatDrawer.tsx](frontend/src/ChatDrawer.tsx), [AssistantProposalDialog.tsx](frontend/src/AssistantProposalDialog.tsx), [ChatDataAnalysisAnswer.tsx](frontend/src/ChatDataAnalysisAnswer.tsx) |
| 지식·확인 요청 | [SemanticWorkspace.tsx](frontend/src/SemanticWorkspace.tsx), [SemanticCollection.tsx](frontend/src/SemanticCollection.tsx), [confirmationRequests.ts](frontend/src/confirmationRequests.ts) |
| 사례 등록·읽기 | [EvaluationRegistration.tsx](frontend/src/EvaluationRegistration.tsx), [EvaluationKnowledgeSummary.tsx](frontend/src/EvaluationKnowledgeSummary.tsx), [SemanticEditor.tsx](frontend/src/SemanticEditor.tsx) |
| API·계약 | [api.ts](frontend/src/api.ts), [types.ts](frontend/src/types.ts), 해당 `*.generated.ts` |
| 편집 초안·충돌 복구 | [localDraft.tsx](frontend/src/localDraft.tsx), [localDraftStorage.ts](frontend/src/localDraftStorage.ts), [draftReviewState.ts](frontend/src/draftReviewState.ts) |
| 차트 렌더링 | [Charts.tsx](frontend/src/Charts.tsx), [ScriptPlot.tsx](frontend/src/ScriptPlot.tsx), [plotlyRuntime.ts](frontend/src/plotlyRuntime.ts) |

별도 `theme.ts`는 없으며 테마는 `main.tsx`에 있다. 현재 상태 소유자를 찾아 수정하고 같은 기능의 새 전역 store·페이지·편집기를 병행해서 만들지 않는다. React Router, Redux, Tailwind 등 새로운 기반 도구를 작은 수정에 도입하지 않는다.

## 시각적 기준

아래는 현재 `main.tsx`의 값이다. 모든 CSS가 테마만 참조하는 것은 아니므로 실제 적용 스타일도 확인한다.

| 항목 | 현재 기준 |
| --- | --- |
| 주요 색 | `#087f80` 청록, 주요 실행·선택 |
| 보조 색 | `#18364b` 짙은 청회색 |
| 화면 배경 / 표면 | `#f3f6f8` / `#ffffff` |
| 기본 / 보조 글자 | `#183246` / `#72828e` |
| 오류 색 | `#d05959` |
| 글꼴 | Segoe UI, Malgun Gothic, sans-serif |
| MUI 기본 글자 | 13px; 버튼은 대문자 변환 없음 |
| 테마 둥글기 | 기본 10px, 버튼 8px, Dialog 16px |
| 아이콘 | `lucide-react`의 기존 체계 |

흰 표면, 얇은 구분선, 청록 강조, 비교적 조밀한 업무용 화면을 유지한다. 주요 동작은 한눈에 보이게 하고 보조 동작은 기존 텍스트/외곽선 버튼 패턴을 따른다. 글자·여백·반경은 인접한 화면 패턴을 재사용한다. 새 팔레트·외부 웹폰트·장식성 배너를 추가하지 않는다. 작은 글자의 가독성을 고칠 때도 전체 폰트를 일괄 변경하지 않는다.

합성 데이터 참고 화면: [데이터 관리](validation/company-demo-ui.png), [대시보드 카드](validation/company-demo-card.png). 데이터명·개수·기간을 제품 상수로 사용하지 않는다. 최신 실제 화면과 다르면 차이를 먼저 확인한다.

## CSS 변경 원칙

1. 현재 CSS는 대부분 **전역 CSS**다. 파일이 나뉘어도 해당 컴포넌트에만 적용되는 것이 아니다. `rg`로 같은 class/selector를 쓰는 다른 화면을 확인한다.
2. 특정 화면 변경은 고유한 최상위 class 아래로 범위를 제한한다. 전역 `button`, `table`, `h2`, `.MuiDialog-paper` 등을 덮어써 한 화면만 고치지 않는다. 공통 규칙 변경이면 다른 사용 화면도 확인한다.
3. CSS와 MUI `sx`에 같은 규칙을 중복 정의하지 않는다. 적용 순서·선택자 우선순위를 확인해 원래 규칙을 고친다. `!important`로 증상을 누적시키지 않는다.
4. MUI Dialog/Popover/Menu는 portal로 표시될 수 있다. 실제 DOM과 적용 범위를 확인하고 해당 인스턴스의 props/class 또는 기존 theme override를 사용한다. z-index를 크게 올려 덮어 가리지 않는다.
5. 기존 파일이 압축된 긴 줄이어도 관련 없는 부분을 포맷·재작성하지 않는다. 변경 범위를 검토할 수 있게 유지한다.
6. 긴 한글·컬럼명·SQL, 빈 데이터, 로딩·오류·비활성·선택·키보드 focus도 확인한다. 아이콘 버튼에는 설명/접근 가능한 이름을 제공한다.

## 상태·API·저장 원칙

- 일반 요청은 `api.ts`의 `api/post`를 사용한다. `/api` 접두사, 같은 출처 인증, workspace token, 오류 처리를 생략한 별도 `fetch` 경로를 만들지 않는다. 챗은 기존 `streamChat`의 SSE·취소 흐름을 유지한다.
- 서버 상태는 React Query, 편집 중인 값은 기존 편집기/로컬 초안이 소유한다. 서버 데이터·편집 초안·미리보기 결과를 같은 상태로 덮어쓰지 않는다.
- query key와 무효화 범위를 확인한다. 현재 모든 key에 workspace가 들어가지는 않으며 `App.tsx`가 DB 변경 시 취소·캐시 제거·선택 초기화를 관리한다. 새 요청도 여기에 포함하고 사용자·프로젝트·조건·버전 구분을 유지한다.
- DB/프로젝트/계정 전환 후 늦게 돌아온 이전 응답이 새 화면에 적용되지 않도록 기존 취소·범위 검사를 유지한다. 이전 프로젝트의 데이터셋 선택을 새 프로젝트에 넘기지 않는다.
- 카드 저장은 실제 미리보기/검증 후 기존 저장 경로로 처리한다. AI 제안의 데이터셋+카드 적용을 여러 임의 요청으로 나눠 원자성을 깨지 않는다.
- 409 충돌을 자동 덮어쓰기·무한 재시도로 숨기지 않는다. 초안 비교·복구, 버전 확인, 성공 후 `clearSaved`를 유지한다. 편집기 닫기·탭 전환을 저장으로 처리하지 않는다.
- 통계·ML·승인 상태·권한·프로젝트 필터를 FE에서 새로 판정하지 않는다. 서버 결과를 표시하고 기존 계약으로 요청한다. 버튼 숨김만으로 권한을 보장한다고 가정하지 않는다.
- 확인 요청 상태는 `confirmationRequests.ts`를 재사용한다. 추가 설명 필요 / 승인 필요 / AI 정리 중 / 반영 완료 / 보류 / 처리 확인 필요를 각 화면에서 따로 추정하지 않는다.
- `*.generated.ts`는 수동 수정하지 않는다. 파일 헤더의 생성기와 백엔드 계약을 함께 확인한다. `generate_chat_feature_contracts.py --check`는 그 생성기가 관리하는 일부 계약만 검사한다.
- StrictMode, query retry 정책, 미리보기 지연/취소를 오류 회피용으로 제거하지 않는다. 효과 함수가 중복 실행되어도 잘못 저장하지 않도록 원인을 확인한다.

## 차트·배치·반응형

- Plotly의 기존 렌더링과 `plotlyRuntime.ts` 지연 로딩을 유지한다. 브라우저에서 Python을 실행하거나 새 차트 엔진으로 바꾸지 않는다.
- 격자 좌표·카드 크기는 `dashboardGrid.ts`와 저장된 layout을 따른다. CSS 위치 조정으로 저장 좌표와 보이는 위치가 달라지게 만들지 않는다. 새로고침과 배치 저장은 별개다.
- 480px 이하에서는 한 열로 표시하고 배치 편집을 제한하는 현재 정책을 유지한다. 팀 공용 layout을 모바일 배치로 덮어쓰지 않는다.
- 실제 시연 해상도·확대율에서 확인한다. 기본 예시는 1366×768, 1920×1080, 폭 800px·480px이다. 고정 너비 때문에 표·모달·채팅 입력이 잘리지 않게 한다.
- 표 내부 스크롤, 긴 SQL, Dialog 저장/취소, 차트 resize, 오른쪽 채팅의 겹침을 확인한다. overflow를 전역 숨김 처리해 문제를 감추지 않는다.

## 수정 전후 확인과 검사

수정 전 화면 경로·프로젝트·선택 항목·화면 크기를 기록하고 같은 조건에서 비교한다. 소스만 수정한 뒤 이전 `dist`를 검증하지 않는다. `start-app.ps1 -NoBuild`는 소스를 빌드하지 않는다.

프로젝트 루트에서:

```powershell
node --experimental-strip-types --test frontend/tests/*.test.mjs
```

`frontend` 폴더에서, 명령별 종료 코드를 확인하며 순서대로:

```powershell
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

API 계약을 변경했다면 프로젝트 루트에서 해당 생성기 검사와 백엔드 검사를 추가한다.

```powershell
.\.venv\Scripts\python.exe -X utf8 validation/generate_chat_feature_contracts.py --check
```

| 변경 영역 | 관련 FE 검사 예시 | 실제 사용자 흐름 |
| --- | --- | --- |
| 프로젝트/DB/선택 | `workspaceState`, `datasetInventory`, `chatDataAnalysis` | 공통→프로젝트 A→B, DB 변경 후 목록·챗 선택 |
| 카드·피벗 편집 | `cardEditing`, `cardExpressions`, `pivotSettings`, `localDraftStorage` | 수정→미리보기→저장→재열기, 취소·충돌 |
| 격자·기간·갱신 | `dashboardGrid`, `dashboardPeriod`, `dashboardRefresh` | 배치 저장·재조회, 기간 변경, 좁은 화면 |
| 채팅·확인 요청 | `chatProposal`, `confirmationRequests`, `evaluationExecution`, `analysisKnowledgeJourney` | 질문→답변→검토·승인→결과/분석 복귀 |

파일은 `frontend/tests/<이름>.test.mjs`다. 단위 검사가 모든 DOM 배치·서버 연결을 보장하지는 않는다. 실제 사용자 흐름과 로딩/빈 결과/오류를 별도로 확인한다. 문구·간격 변경에 동작과 무관한 테스트를 늘리지 않는다. 데이터·저장 변경에는 해당 백엔드 검사도 필요하다.

결과에는 원인, 변경 파일·사용자 동작, 실행한 검사, 실제 화면 확인, 미확인 조건을 남긴다. 회사 데이터가 담긴 스크린샷은 외부 공유/Git 포함 없이 승인된 사내 경로에서 관리한다.
