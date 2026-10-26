# 앱 AI 피벗 실측 검증 — 2026-10-22

현재 앱에 연결된 실제 LLM에 브라우저 채팅으로 자연어를 입력하고, AI가 작성한 SQL을 수정하지 않은 채 검증·데이터셋 제안·미리보기까지 실행했다. 테이블, 행 기준, 열 항목, 시간과 NULL 처리 규칙을 요청에 명시했다. 모델 응답을 흉내 내는 테스트가 아니다.

| 시나리오 | 입력 및 조건 | 결과 |
|---|---|---|
| 단일 테이블 피벗 | `inline_measurements`, wafer_id 1·2, 행 기준 lot_code·wafer_id·site_x·site_y | 10행, 전체 기준값 일치 |
| JOIN 후 피벗 | `inline_measurements m JOIN wafers w ON m.wafer_id = w.id`, w.project_id 1·wafer_number 1·2, 행 기준에 w.lot_code·wafer_code·wafer_number 사용 | 20행, 전체 기준값 일치 |

두 경우 모두 metric_name의 CD·resistance를 value_cd·value_resistance로 펼친다. AI는 각 행 기준+항목별로 `ROW_NUMBER() OVER (... ORDER BY measured_at DESC, id DESC)`를 적용해 마지막 행을 먼저 선택하고, 선택된 값만 조건부 집계했다. 전체 이력의 최댓값을 마지막 측정값으로 잘못 사용하지 않았다.

## 검증 방법과 결과

- 원천 행을 읽어 Python에서 별도로 각 그룹의 마지막 (측정 시각, id)을 선정한 뒤 AI SQL 결과의 모든 행·값·순서·컬럼을 비교했다. 10행과 20행 모두 일치했다.
- `/api/datasets/preview`가 보존한 SQL과 브라우저에서 추출한 AI 원문이 동일하다. 원본 SQL에 수정이나 보정은 하지 않았다.
- 데모 데이터에 충분하지 않은 사례는 물리 테이블 입력만 타입이 지정된 `VALUES` CTE로 교체하고, AI가 생성한 JOIN·필터·마지막 값 선택·피벗 로직을 PostgreSQL에서 실행했다. 이 추가 검증은 AI에게 다시 질의한 테스트가 아니라, 생성된 SQL의 로직 검증이다.
- 추가 사례: CD와 resistance의 서로 다른 마지막 시각, 동일 시각의 큰 id 우선, 과거 값/id가 더 커도 측정 시각 우선, 마지막 NULL의 과거 값 대체 금지, 항목 누락, NULL 시각/행 기준 제외, 0·음수 유지, wafer 간 분리.
- JOIN 추가 사례: 오른쪽 테이블의 행 기준과 필터 반영, 이전 대화의 wafer_id 1·2 조건 제거, 두 번째 lot의 wafer 포함, 미매칭 행·다른 프로젝트·다른 wafer_number 제외. 단일 3행·JOIN 5행의 합성 기준값 모두 일치했다.
- 원천 조회 트랜잭션의 `transaction_read_only = on`을 확인했다. 원천 DB 테이블·뷰·데이터 생성/수정 없음.
- 브라우저 오류·경고 없음. 데이터셋 생성/적용은 누르지 않았으며 기존 데이터셋 10개·카드 7개·공용 대시보드 v35의 전체 응답이 테스트 전후 동일하다.

## 현재 지원 범위

AI의 피벗은 실행 가능한 **SQL 데이터셋 초안**으로 생성된다. 단일 테이블과 JOIN 후 피벗 모두 검증·조회가 가능하지만, AI 출력에 `pivot` 설정 메타데이터가 없으므로 간편 편집기의 피벗 폼으로 자동 복원되지는 않는다. 두 초안 모두 SQL 편집 경로를 사용한다. 이번 작업에서는 제품 코드를 변경하지 않았다.

이번 검증은 위 조건을 명시한 두 자연어 요청에 대한 결과이며, 데이터셋 영구 저장이나 대량 데이터 성능 측정은 포함하지 않는다.

## 기록

- 요청 원문: `.local/ai-pivot-single-request.txt`, `.local/ai-pivot-join-request.txt`
- AI SQL 원문: `.local/ai-pivot-single.sql`, `.local/ai-pivot-join.sql`
- 미리보기 응답: `.local/ai-pivot-single-preview.json`, `.local/ai-pivot-join-preview.json`
- 비교 결과: `.local/ai-pivot-verification.json`
- 설정 불변 확인: `.local/ai-pivot-before.json`, `.local/ai-pivot-after.json`
- 화면: `.local/ai-pivot-join-results.png`
- 재검증: 프로젝트 루트에서 `PYTHONPATH=backend`로 `validation/verify_ai_pivots.py` 실행. 위 SQL 파일, 작업 전 스냅샷, 실행 중인 로컬 앱과 현재 데모 DB가 필요하다. 재검증 자체는 LLM을 호출하지 않는다.
