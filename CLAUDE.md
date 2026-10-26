# Project Manager · Claude 작업 시작점

공통 작업 지침을 먼저 읽는다. 지원하는 Claude Code에서는 아래 파일을 가져온다.

@AGENTS.md

- 첫 작업에서는 [COMPANY_HANDOFF.md](COMPANY_HANDOFF.md)를 읽고 시스템 구조와 현재 시연 범위를 확인한다.
- FE 변경 전에는 반드시 [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)를 읽는다. 화면이 비슷해 보여도 상태·저장 흐름을 따로 구현하지 않는다.
- 실행·데이터 반입은 [COMPANY_DEMO_GUIDE.md](COMPANY_DEMO_GUIDE.md), 수정 후 검사 선택은 [REGRESSION.md](REGRESSION.md)를 따른다.
- 파일 가져오기가 지원되지 않으면 `AGENTS.md`를 직접 연다. 자동 로드를 가정하지 말고 첫 답변에서 읽은 파일, 요청 범위, 수정 대상과 검증 방법을 간단히 설명한다.
- 기존 문서를 전부 한 번에 읽지 않는다. 위 시작 문서를 읽고 관련 코드와 기능 문서만 추가로 확인한다.

이 파일은 개발 보조 Claude의 지침이다. 웹 앱의 LLM 프롬프트나 승인된 AI 지식으로 자동 등록되는 파일이 아니다.
