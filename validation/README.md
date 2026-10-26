# 검증 코드와 기록

Git에는 검증용 코드와 Markdown 기록을 포함한다. 실행 과정에서 얻은 DB 상태·모델 응답·대화·조회 결과 JSON, 텍스트 로그, 화면 캡처는 원칙적으로 제외한다. 과거 기록에 나오는 결과 파일은 로컬 실행 산출물이며 새 clone에 없을 수 있다.

FE 지침에서 사용하는 `company-demo-ui.png`, `company-demo-card.png` 두 장만 포함한다. `verify_company_demo_flow.py`가 직접 생성한 합성 데이터로 촬영했고 포함 전 화면을 확인했다. 실제 회사 데이터의 캡처를 같은 이름으로 덮어써 커밋하지 않는다.

현재 시연 검증의 시작점은 [company-demo-readiness.md](company-demo-readiness.md)다. 실행 절차는 [COMPANY_HANDOFF.md](../COMPANY_HANDOFF.md), 연결 기능 검사 선택은 [REGRESSION.md](../REGRESSION.md)를 참고한다.

이 폴더에는 이전 기능 작업의 진단·실모델 호출·승인 스크립트도 있다. 파일을 일괄 실행하지 말고 대상 DB·입력·외부 호출·쓰기 동작을 확인한다. 과거 실행 ID나 PC 경로가 남은 스크립트는 현재 회사 환경에 곧바로 사용할 수 있다는 뜻이 아니다. 원시 측정 행은 외부 모델로 보내지 않는다.
