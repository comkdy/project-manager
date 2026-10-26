# 원천 장비 컬럼과 공정별 측정 항목 — 2026-10-25

## 반영 내용

- 로컬 합성 DB `wafer_fab_demo`의 **원천 `public.wafer_his`**에 `eqp_id text NOT NULL`을 추가했다. XXXA/XXXB/XXXC 파티션에도 상속된다.
- 7,627행에 `SYN_oper_B_EQP01` 등의 합성 검증용 장비 ID를 채웠다. 공정마다 3개 장비로 재현 가능하게 배정한다. 장비 배정은 측정 결과와 난수 생성 순서에 의존하지 않으며 Inline/EPM 수치를 생성하거나 수정하지 않는다.
- 기존 원천 자동 갱신으로 공통 wafer_his v2, XXXA v4, XXXB v2, XXXC v2에 컬럼을 반영했다. 프로젝트별 lot_cd 제한을 유지했다. 사용자가 작성한 결합 SQL 및 기존 분석 데이터셋 v2는 변경하지 않았다.
- Inline은 저장된 SQL의 **operation+parameter**로 식별한다. 같은 MAIN이라도 서로 다른 공정이면 별도 항목이며, 다른 JOIN 쪽의 공정명을 측정값에 붙이지 않는다. 항목이 확정되지 않으면 한 항목으로 계산하는 것을 차단하고, 추천·챗은 기존 확인 요청으로 연결하도록 지시한다.
- 워커 결과·챗 결과·승인 근거 설명에 `Inline oper B CD / MAIN`, `공정 oper B / Recipe`, `EPM PARA3`를 표시한다. 원래 계산은 이미 `oper B CD / MAIN`으로 한정돼 있었다. 이번 변경은 계산 대상의 명확한 식별과 설명이며 기존 수치를 다시 계산하거나 승인 내용을 덮어쓰지 않았다.
- 완료된 근거는 사용하던 컬럼·자료 정의가 그대로인 **원천 컬럼 추가만** 허용한다. 컬럼 삭제/타입 변경, 데이터셋 버전·연결·권한·프로젝트 변경은 계속 차단한다. 아직 실행 전인 작업은 기존의 엄격한 확인 기준을 유지한다.

## 실제 데이터 보존

`demo/fab/add_equipment.py`는 고정된 로컬 런타임/DB 소유자/합성 DB 표식을 확인한 뒤 단일 거래로 변경했다. 원천 읽기 전용 앱 계정의 권한은 변경하지 않았다.

- 원래 wafer_his 7개 컬럼의 전후 전체 행 해시·행 수 일치. 장비 누락 0행.
- XXXA 2,477행 / XXXB 2,536행 / XXXC 2,614행.
- 기존 앱 32개 테이블 1,711행 중 요청된 원천 메타데이터 1행만 갱신. 기존 데이터셋 버전·카드·프로젝트·승인 지식·분석 실행·대화의 값 보존, 삭제 0행.
- 추가 기록은 원천 데이터셋 버전 4개, 검증 대화 2개와 해당 감사/조회 컨텍스트다. 새 지식을 자동 승인하지 않았다.
- [원천 변경 기록](wafer-equipment-migration.json), [데이터 보존 기록](wafer-equipment-preservation.json).

## 검증

- 백엔드/합성 데이터 회귀 **448 passed**, exit 0 (52.38초): 분석·추천·질문·승인·데이터셋·카드·채팅·프로젝트 경계·작업 상태/재시도·취소. [전체 로그](wafer-equipment-backend-tests.txt).
- 실모델 검증에서 발견한 후속 문단의 공정명 생략과 근거 없는 '수십 배' 표현을 수정한 뒤 관련 검사 **57 passed**, exit 0 (4.60초). 448개와 중복되므로 합산하지 않는다.
- 실제 화면에서 새 `measurement_identity` 인용이 설명 전체를 숨기던 표시 계약 누락을 발견하고 수정했다. 인용이 다른 공정으로 바뀌거나 사라지면 설명을 숨기는 검사도 추가했다.
- 화면 로직 **232 passed**, TypeScript/Vite 빌드 성공. [화면 검사 로그](wafer-equipment-frontend-tests.txt). 기존 대용량 번들 경고만 있다.
- `verify_dataset_boundaries_live.py`: 공통/프로젝트 범위 분리 및 XXXA 기존 카드 7개 실제 조회 성공.
- `verify_wafer_equipment.py`: 전체 장비 배정·물리 컬럼·원천 데이터셋·기존 메타데이터 보존 확인, exit 0.

## 실제 모델과 화면

사용자가 승인한 범위의 검증 질문과 승인 근거 요약만 현재 OpenRouter 모델 `z-ai/glm-5.3-flash`에 전송했다. 원시 측정 행은 보내지 않았다. `verify_equipment_chat_knowledge.py`는 전송 근거 객체에 raw rows/preview/records 등이 없음을 검사한다.

최종 대화 `9c66d53e-9511-4f04-8c4d-af72625f3f00`은 승인 지식 `06c319ee-2bb4-40b2-9d79-c76da7cc6fb2` v1과 원본 ML 실행 `4591b8bf-fb52-45a0-8626-0e1fd4bd176f`를 인용했다. Inline 관련 문단은 `oper B CD / MAIN`으로 설명하고, 공정이 확정되지 않은 과거 관측은 미확인이라고 구분했다. 인과 효과로 확정하지 않았고 재접속 후 동일한 설명·인용이 복원됐다.

[최종 챗 검증](wafer-equipment-chat.json), [모델에 제공한 근거 요약](wafer-equipment-chat-evidence.json).

새 eqp_id는 원천에 추가됐지만 **기존 승인된 ML의 설명 변수에는 포함되지 않는다**. 따라서 기존 결과를 장비 영향 분석 결과라고 해석할 수 없다. 장비를 포함한 분석은 해당 공정의 eqp_id를 결합한 데이터셋으로 별도 실행해야 한다.

실제 브라우저에서 복원된 설명과 근거 펼치기도 확인했다.

![XXXA 원천 wafer_his의 eqp_id](wafer-equipment-source.png)

![공정과 parameter를 함께 표시한 챗 설명](wafer-equipment-chat.png)
