# 평가 사례 자동 정리 오류 수정

## 확인한 원인

- 기존 합성 사례 1(1,461자) 재현: HTTP 200, `finish_reason=length`, completion 5,000 tokens, content 0자. 연결 실패가 아니라 출력 한도 소진이었다. 기존 코드는 이를 일반 형식 오류로 숨겼다.
- 모델 설정 조정 후 실제 API 연결 검사에서 원문을 재작성한 quote/value가 근거 검증을 통과하지 못했고, 후속 분석 계획을 미해결 질문으로 처리하는 문제도 재현했다. 검증을 우회하지 않고 생성 방식을 수정했다.

## 변경 및 영향

- 공용 지식 수집 전송: OpenRouter에만 JSON 응답·최소 추론 설정. 내부 호환 서버에는 공급자 전용 옵션을 보내지 않는다. length 종료 시 결과를 폐기하고 출력 상한을 5,000 → 10,000으로 한 번만 재시도한다. 인증·한도·연결·시간 초과·형식 오류는 고정된 안전한 메시지로 구분한다.
- 평가 사례 전용: AI는 원문 구절 ID를 선택한다. 서버가 해당 답변의 정확한 문구를 value/quote로 복원하고 기존 근거 검증을 수행한다. 인용문을 다시 쓰지 않으며 임의 참조와 analysis_result 주입은 거절한다. 원문 전체와 기존 답변 ID는 보존한다.
- 다음 분석 제안/명시된 한계는 관측 자체의 등록을 막는 질문과 구분한다. 관측 정보가 실제로 없으면 기존 추가 질문 경로를 이용한다.
- 외부 API·DB 스키마·프런트엔드 계약 변경 없음. 실제 앱의 기존 초안·질문·프로젝트·데이터셋·카드·채팅 데이터를 변경하지 않는다. 승인 권한 및 사용자 검토 단계 유지.
- 화면 통합은 이 변경에 포함하지 않았다. 사용자에게 질문함과 검토 대기를 ‘확인 요청’으로 통합하고 필요한 행동을 구분하는 방안을 설명했다.

## 검증

- 관련 백엔드 234개 통과: 전송/근거 복원, 지식 수집·대상·승인, 평가 사례 검색, 추가 분석·결과 재등록, 채팅·질문, 프로젝트 데이터셋, 쿼리, 대시보드 기간.
- 실제 설정된 제공자 + 임시 메모리 DB 통합 검사 4개 통과(26.94초): 기존 합성 보고서 3건의 등록 → 자동 정리 → 검토 → 승인 → 사례 검색, 관측 누락 사례의 추가 질문 → 답변 → 자동 재정리 → 승인.
- 모델·추출 함수를 대체하지 않았으며 초안을 수동으로 만들지 않았다. 검증 중 승인 전 검색 제외, 일반 사용자 승인 금지, 승인 후 검색, 중복 등록 방지를 함께 확인했다.
- 기존 합성 사례 3건은 기존 검토 대기 상태로 보존했다. 실제 앱 사례를 검증 목적으로 승인하지 않았다.
- 앱 재시작 후 정상 응답 확인. 실제 앱 데이터 보존: 관리 테이블 30개/1,226행의 전후 해시가 모두 동일하다. 결과는 `evaluation-autostructure-metadata-before.json` / `evaluation-autostructure-metadata-after.json`.

재현 자료: `test_evaluation_live_structure.py`(명시적 PM_LIVE_STRUCTURE=1 필요), `evaluation-live-structure-1.json`~`3.json`, `evaluation-structure-before.json`, `.local/evaluation-live-structure-tests.log`, `.local/evaluation-autostructure-regression.log`.

제공자의 추론/출력 공통 한도와 length 종료 설명: [OpenRouter Reasoning Tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#reasoning-tokens-and-max_tokens).
