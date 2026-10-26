# 질문함·검토 대기 통합

## 구현 범위

- AI 지식의 탐색을 ‘지식 목록 / 확인 요청’으로 통합했다. 기존 inbox 진입 계약과 질문 ID는 유지해 프로젝트·채팅·분석 결과의 연결을 보존했다.
- 질문 하나에 원문·답변 이력·추가 설명·자동 정리·관리자 검토·승인 결과를 함께 표시한다. 추가 설명 필요 / 승인 필요 / AI 정리 중 / 반영 완료 / 보류 / 처리 확인 필요를 구분하고 필요한 확인별 필터를 제공한다.
- 답변·반려·승인으로 단계가 바뀌어도 열린 항목은 유지된다. 프로젝트 범위가 다르면 선택된 항목도 표시하지 않는다. 현재 질문에 연결된 정확한 답변·초안만 가져오고, 전체 검토 초안 목록의 주기적 조회는 제거했다.
- 관리자만 검토 API·승인 화면에 접근한다. 기존 revision 검사, 원문 근거, 데이터셋 변경 시 승인 차단, 로컬 임시 저장을 유지한다. 새 답변이 이전 초안을 대체할 때 작성 중 검토 의견을 보존하고 기존 초안 승인을 막으며 최신 초안으로 명시적으로 전환할 수 있다.
- 관리자가 초안의 미정 정보를 해결하면 같은 요청의 추가 확인 목록도 갱신한다. 실제 원문·최초 초안은 유지한다. DB 스키마 변경·데이터 마이그레이션은 없다.
- 프로젝트·데이터셋·쿼리·카드·채팅의 기능 변경 없이 연결 문구를 ‘확인 요청’으로 맞췄다.

## 영향 및 검증

의존 경로: `App` → `SemanticWorkspace` → `SemanticCollection` → 기존 질문/초안 API. `analysisSelection`의 정확한 질문·답변 검증과 분석 복귀 계약, `draftReviewState`의 충돌 검사를 재사용한다. `collector_router.update_draft`의 후속 질문 갱신만 백엔드에서 변경했다.

- 관련 백엔드 **189개 통과**: 지식 수집/질문 대상, 평가 사례/분석 결과, 분석 및 채팅, 프로젝트 데이터셋·쿼리·대시보드 기간. 초안 보완 후 같은 요청의 필요 행동 갱신 회귀 검사를 추가했다.
- 프런트엔드 전체 **216개 통과**: 요청 단계·필터와 상태 변경 후 선택 유지, 분석 복귀, 검토 충돌, 기존 카드·채팅·데이터 기능. TypeScript 검사 및 Vite 빌드 성공.
- 임시 앱 브라우저: 등록 → 추가 설명 → 같은 항목의 검토 → 반려 → 재답변 → 동시 답변/검토 의견 보존 → 초안 수정 → 승인 → 같은 항목의 반영 완료. 프로젝트 범위 변경과 390px 화면 넘침 없음 확인. 브라우저 오류 없음.
- 기존 분석 브라우저: 데이터셋·쿼리·카드 편집, 채팅 사례 검색, 실제 합성 데이터 그룹 통계·독립 SQL 대조·상관 분석, 결과 검토 요청·승인, 대화 복원·승인 결과 재검색, 실패 실행 재시도 연결 통과.
- 브라우저 QA는 별도 임시 관리 DB와 결정적인 모델 응답을 사용한다. 원천 합성 측정은 읽기 전용으로 조회한다. 이번 작업에서 제공자 설정이나 자동 정리 로직은 변경하지 않았으며 실제 LLM 검증 결과는 `evaluation-autostructure-2026-10-24.md`에 기록되어 있다.

근거: `confirmation-requests-ui.json`, `evaluation-execution-ui.json`, `.local/confirmation-requests-backend.log`, `frontend/tests/confirmationRequests.test.mjs`, `.local/confirmation-requests-review.png`, `.local/confirmation-requests-complete.png`, `.local/confirmation-requests-mobile.png`.

실제 앱에 최종 빌드를 반영하고 재시작했다. `confirmation-requests-metadata-before.json` / `confirmation-requests-metadata-after.json` 대조 결과 **30개 테이블·1,226행의 내용 해시가 모두 동일**했다. 운영 사례는 QA 중 승인하거나 수정하지 않았다. 임시 앱과 임시 DB는 정리했고, 실행 중인 앱에서 ‘확인 요청’과 기존 사례의 ‘승인 필요’ 표시를 확인했다(`.local/confirmation-requests-live.png`).
