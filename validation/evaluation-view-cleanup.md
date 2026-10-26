# 평가 사례 공통 편집 항목 정리 — 2026-10-25

평가 사례 상세에서 일반 정의 편집용 이름·업무 영역·업무 설명·동의어·미정 사항·참조 정의·변경 이유 입력과 직접 초안 저장/승인 버튼을 숨겼다. 적용 범위, 사용자 관계, 승인된 용어 연결, 계산 근거, 보존된 원문 요약·조건을 읽는 화면으로 정리했다. 원문·확인 요청 이동, 버전 이력과 기존 버전 폐기 기능은 유지한다. 승인 완료 요청은 기존과 같이 읽기 전용이며, 진행 중 요청의 답변·검토·승인 기능은 변경하지 않았다.

업무 용어·지표·데이터 설명·테이블 관계·검증된 질의의 공통 편집 폼은 유지한다. 평가 사례에 이미 저장된 domain/description/aliases 등은 삭제하거나 변환하지 않는다. 평가 사례에서 숨긴 정의 편집 초안은 새로 자동 저장하지 않으며, 이전 브라우저 보관값도 삭제하지 않는다.

변경 파일: frontend/src/SemanticEditor.tsx, frontend/src/SemanticWorkspace.tsx.

검증:

- `node --experimental-strip-types --test frontend/tests/*.test.mjs`: 종료 0, 234개 통과. 로그 evaluation-view-frontend.log.
- frontend에서 `node node_modules/typescript/bin/tsc -b`: 종료 0.
- frontend에서 `node node_modules/vite/bin/vite.js build`: 종료 0. 기존 Plotly 크기 안내는 빌드 오류가 아니다.
- `/api/semantic/items` 읽기 전용 해시 비교: 기존 3개 지식의 content/version/status/binding 모두 일치. evaluation-view-before.json / evaluation-view-after.json.
- 서버 코드, 데이터 마이그레이션, 프로젝트·데이터셋·카드 편집, LLM 프롬프트/호출은 변경 없음. 이번 검증에서는 모델을 호출하거나 데이터를 외부로 전송하지 않음.

최종 브라우저 검증 결과와 화면은 같은 validation 폴더의 evaluation-view-ui.json / evaluation-view-cleanup.png에 기록한다.
