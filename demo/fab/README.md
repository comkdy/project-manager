# 사내 스키마 기반 합성 테스트 DB

사용자가 제공한 테이블 구조와 흐름을 바탕으로 생성한 **합성 데이터**다. 사내 데이터의
복사본이나 실제 공정 규격이 아니다. 값의 평균·편차·recipe 효과는 테스트 가정이다.

새 DB는 `wafer_fab_demo`이며 기존 `wafer_demo` 및 앱 관리 DB `wafer_app`과 분리된다.
앱의 **워크스페이스 관리 → DB 설정**에서 활성 DB를 전환할 수 있다. 기존 DB의
데이터셋·쿼리·대시보드는 보존된다. 기본 6개에 일정용 테이블 3개를 추가하여 현재는
9개 기본 데이터셋이며, 대시보드는 그대로 보존된다.

## 테이블과 데이터 단위

| 테이블 | 한 행의 의미 | 주요 컬럼 |
|---|---|---|
| `inline_data` | wafer의 위치·공정·parameter별 측정 이벤트 | lot_id, wf_id, die_x, die_y, operation, parameter, value, end_dt_tm, lot_cd, record_id |
| `epm_data` | wafer의 위치·parameter별 전기 특성 측정 이벤트 | lot_id, wf_id, die_x, die_y, parameter, value, end_dt_tm, lot_cd, record_id |
| `lot_his` | lot의 공정 진행 이벤트 | lot_id, operation, remark, end_dt_tm, lot_cd, record_id |
| `wafer_his` | wafer의 공정·recipe 진행 이벤트 | lot_id, wf_id, operation, recipe, end_dt_tm, lot_cd, record_id |
| `reject_his` | wafer의 폐기 또는 TEM 시편 제작 이력 | lot_id, wf_id, operation, reject_reason, remark, end_dt_tm, lot_cd, record_id |
| `tem_his` | TEM 시편 촬영 이벤트 | lot_id, wf_id, reject_id, serial_num, end_dt_tm, lot_cd, record_id |
| `lot_plan` | lot별 예정 공정 순서 | lot_id, operation_seq, operation, lot_cd |
| `operation_tat` | 완료 공정의 경과 시간(hour) | lot_id, operation, tat, lot_cd |
| `current_status` | 진행 중 lot의 현재 공정, lot당 1행 | lot_id, operation, remark, lot_cd |

- 모든 테이블은 `PARTITION BY LIST (lot_cd)`이며 `XXXA/XXXB/XXXC`의 3개 파티션을 가진다.
  부모 테이블 9개 + 물리 파티션 27개다. 앱은 부모 테이블만 데이터셋으로 자동 등록한다.
- `lot_cd = left(lot_id, 4)`를 CHECK로 강제한다. `XXXA1`~`XXXA20`, `XXXB1`~`XXXB20`,
  `XXXC1`~`XXXC20`의 완료 lot 60개에 그룹별 `21`~`26`의 진행 중 lot 18개를 추가했다.
  총 78개 lot이며 lot마다 wafer가 10~15장이다.
- `wf_id`는 lot 안에서 1부터 시작하는 정수다. 다른 lot의 같은 wf_id를 같은 wafer로
  취급하면 안 된다. wafer 대응 키는 `(lot_cd, lot_id, wf_id)`, 위치 대응에는
  `(die_x, die_y)`를 추가한다.
- `end_dt_tm`은 PostgreSQL `timestamptz`이며 UTC로 저장한다. 폐기 테이블에서는 폐기
  시각, TEM 테이블에서는 촬영 시각이다. 앱 표시 타임존과 구분한다.
- 기존 이력·측정 테이블의 `record_id`는 동일 시각의 마지막 기록을 결정하기 위한 기술 컬럼이다. 생성기에서는
  테이블 전체에서 고유하며 증가한다. DB의 기본 키는 파티션 키를 포함한
  `(lot_cd, record_id)`다. 복합 기본 키를 단일 키라고 잘못 자동 추정하지 않는다.
- 일정 테이블은 자연 키를 사용한다. lot_plan은 `(lot_cd,lot_id,operation_seq)`,
  operation_tat는 `(lot_cd,lot_id,operation)`, current_status는 `(lot_cd,lot_id)`다.
  이 fixture는 동일 공정 재방문이 없는 경로이며 계획·TAT·현재 공정을 외래 키로 연결한다.
- `tat`는 **시간(hour) 단위 경과 시간(대기 포함)**이며 장비의 순수 가동 시간이 아니다.
  소수 셋째 자리까지 저장하며, 예를 들어 `1.5`는 1시간 30분이다.
  완료 이력 간 시간 차를 사용한다. 시작을 알 수 없는 기존 lot의 첫 공정은 TAT가 없고,
  추가 WIP는 생성 시 시작 시각을 알기 때문에 첫 공정 TAT도 있다.
- current_status에도 `lot_cd`를 추가해 기존 테이블과 동일하게 파티션한다.
  세 일정 테이블에 없는 시작/완료 시각을 실제 값인 것처럼 만들어 넣지 않았다.
- `reject_his.reject_reason`은 `TEM 시편 제작` 또는 `단순 폐기`다. `tem_his.reject_id`는
  해당 폐기 기록을 참조하며 lot·wafer까지 포함한 외래 키로 다른 wafer 연결을 방지한다.
- 같은 wafer에서 여러 TEM 시편이 나올 수 있고 같은 serial_num을 재촬영할 수도 있다.
  촬영 한 건이 한 행이므로 serial_num만으로 행을 유일하게 식별하지 않는다.

## 생성된 내용

기본 seed `20261022`, 기준일 `2026-10-22`. 일정 확장 후 총 78개 lot / 975장 wafer.
측정 시 wafer당 49개 위치이며, 아직 해당 공정에 도달하지 않은 WIP의 측정값은 없다.

| 테이블 | 행 수 |
|---|---:|
| inline_data | 523,893 |
| epm_data | 186,269 |
| lot_his | 65,955 |
| wafer_his | 7,627 |
| reject_his | 34 |
| tem_his | 30 |
| lot_plan | 78,000 |
| operation_tat | 65,955 |
| current_status | 18 |

- 현재 공정 계획은 랏당 1,000단계이며 기존 9개 공정을 주요 이정표로 보존한다.
  skip 50단계를 제외한 950단계의 총 경과 시간은 30~60일이다.
- Inline parameter는 `MAIN/SUB1/SUB2`, EPM은 `PARA1`~`PARA5`, recipe는 `RCP1`~`RCP3`다.
- 8주 이상에 걸친 데이터로 최근 4주 기간 필터 전후를 비교할 수 있다. 기준일의 최근
  4주 시작일은 2026-09-28이다. 고밀도 확장 시 측정 시각도 새 공정 완료 시각에
  맞춰 이동하므로 최초 9단계 fixture의 기간별 행 수와 달라진다.
- 각 공정의 측정 시각이 다르고, 일부 재측정 및 동일 시각 기록이 있다. 마지막 NULL과
  아예 누락된 측정 그룹도 포함한다. 마지막 값 선택 시 `ORDER BY end_dt_tm DESC,
  record_id DESC`를 사용하며 마지막 NULL을 과거 값으로 대체하지 않는다.
- 공정 중 폐기 wafer는 이후 공정·측정에 나타나지 않는다. TEM 의뢰 중 6장은 아직 촬영
  이력이 없다. TEM 대상의 일부는 여러 시편·재촬영을 가진다.
- wafer/lot/위치의 공통 편차를 Inline과 EPM에 공유해 상관 분석을 테스트할 수 있다.
  `XXXB7`에는 합성 특성 이동과 관련 remark를 넣었다. 실제 공정 이상 정의는 아니다.

## 조회·피벗 주의점

- `lot_cd` 조건을 넣으면 PostgreSQL이 해당 파티션만 조회한다. `lot_id`만으로는 CHECK
  조건의 의미를 추론해 파티션을 자동 선택한다고 가정하지 않는다.
- Inline 피벗에서 `operation`을 열로 펼치고 `parameter='MAIN'`처럼 조건을 지정하거나,
  parameter를 행 기준에 포함해야 서로 다른 parameter 값이 섞이지 않는다.
- 서로 다른 측정 테이블의 원시 이력을 바로 JOIN하면 재측정 횟수에 따라 행이 증식할
  수 있다. 각 테이블에서 필요한 parameter와 마지막 값을 먼저 정한 뒤 위치 키로 JOIN한다.
- wafer_his와 결합할 때도 operation과 시간 조건을 고려한다. lot_his의 remark를 붙이는
  요청은 lot 단위라는 점을 유지한다. [예시 SQL](examples.sql)을 참고한다.
- 간편 피벗에서는 행 기준 `lot_cd, lot_id, wf_id, die_x, die_y`, 열 기준 `operation`,
  값 `value`, 시간 `end_dt_tm`, 동일 시각 기준 `record_id`를 선택할 수 있다.
  JOIN 후 피벗은 SQL 편집기를 사용한다.

## 실행과 검증

관리되는 로컬 PostgreSQL과 앱을 실행한 상태에서 프로젝트 루트에서 실행한다.

```powershell
.\.venv\Scripts\python.exe demo/fab/seed.py --anchor 2026-10-22 --activate
.\.venv\Scripts\python.exe demo/fab/add_schedule.py
.\.venv\Scripts\python.exe demo/fab/verify.py
.\.venv\Scripts\python.exe demo/fab/verify_schedule.py
.\.venv\Scripts\python.exe -m pytest demo/fab/test_generator.py demo/fab/test_schedule.py -q
```

이미 생성된 DB를 검증 후 활성화할 때는 `seed.py --activate-existing`를 사용한다.
다른 규모는 **처음 생성할 때** `--lots-per-group`으로 지정한다. seed와 anchor를 같게
주면 같은 값을 재현한다. 이 생성기는 기존 테이블을 덮어쓰거나 삭제하는 옵션이 없다.
일정 확장 `add_schedule.py`도 한 번만 실행하며 이미 생성돼 있으면 중단한다.
이전 분 단위 fixture는 `python demo/fab/migrate_tat_hours.py`로 시간 단위로 변환한다.
DB에 저장된 단위 표식을 확인하므로 재실행해도 다시 나누지 않는다. 변환 전 값은
`.local/fab-tat-minutes-backup.json`에 보관한다.
기존 6개 테이블의 원본 행은 해시와 행 수로 보존을 확인하고, 새 WIP의 완료 이력·측정만
추가한다. 원본 생성 manifest는 보존하고 증분은 `.local/fab-schedule-manifest.json`에 남긴다.
`verify_schedule.py`는 기본 20개 lot/그룹 fixture 기준으로 검증한다.

쓰기 도구는 현재 워크스페이스의 지정된 로컬 runtime·포트·DB 이름·DB 식별 마커를
검증한다. 사내 DB URL이나 앱의 현재 활성 DB를 대상으로 쓰지 않는다. 데이터 생성에만
로컬 관리자 계정을 쓰며 앱은 기존 조회 전용 계정으로 연결한다. `.env` 변경은 없다.

생성 결과 `.local/fab-manifest.json`, 정합성 검증 `.local/fab-verification.json`,
이전 활성 DB와 앱 설정 `.local/fab-previous-workspace.json`에 기록한다.
일정 검증은 `.local/fab-schedule-verification.json`, 확장 전 앱 설정은
`.local/fab-schedule-before-app.json`에 기록한다.

간트차트의 화면·시간 계산·성능 방향은 [간트차트 설계안](gantt-design.md)을 참고한다.
간트차트를 구현했으며 전체/XXXA/XXXB/XXXC 대시보드에 예시 카드를 추가했다.
현재 데이터는 기준일의 고정 WIP 스냅샷이므로 예시 카드에도 같은 기준 시각을 설정했다.
실제 DB에서는 카드 설정의 고정 기준 시각을 비워 조회 시점 기준으로 사용한다.

워크스페이스 관리 → 프로젝트 관리에서 이름, 포함할 lot_cd, 설명과 활성 상태를 관리한다.
설정은 연결 DB별로 저장한다. 단일 lot_cd는 `=`, 복수는 `IN` 조건을 포함한 기본
데이터셋을 생성하며 쿼리 관리에서 SQL을 확인할 수 있다. 프로젝트의 lot_cd를 바꾸면
자동 생성된 기본 쿼리를 새 버전으로 갱신하고 사용자가 직접 편집한 쿼리는 보존한다.


## 1,000단계 간트와 skip 처리 (2026-10-22)

초기 생성/9단계 검증 이후 다음 순서로 확장한다. 확장 후에는 예전 행 수·시각을
가정하는 verify.py/verify_schedule.py 대신 verify_dense_schedule.py를 사용한다.

```powershell
.\.venv\Scripts\python.exe demo/fab/expand_schedule.py
.\.venv\Scripts\python.exe demo/fab/configure_dense_gantt.py
.\.venv\Scripts\python.exe demo/fab/add_dashboard_examples.py
.\.venv\Scripts\python.exe demo/fab/verify_dense_schedule.py
```

- 78개 랏 × 1,000단계. main_oper_yn=Y 500단계, skip_yn=Y 50단계.
  실행되는 단계는 0.334~3시간이며 skip 제외 합계가 랏별 30~60일이다.
- area_id(VIRTUAL/CNC 포함), samp_seq(0/1), main_oper_yn(Y/N), skip_yn(Y/N)를
  lot_plan에 추가했다. 요청한 조건을 모두 적용하면 랏당 표시 공정은 396개다.
- 원본 9개 테이블 CSV gzip 백업은 `.local/fab-dense-backup/`, 변경 명세는
  `.local/fab-dense-manifest.json`에 있다. 파티션 구조는 유지한다.
- Inline/EPM 측정값, wafer/폐기/TEM의 비시각 컬럼·ID·행 수는 해시로 보존을 검증한다.
  이들 시각은 해당 이정표의 이전/새 완료 시각 차이만큼 이동한다.
  wafer_his는 기존 9개 이정표 공정만 포함하며 1,000단계 전체 wafer 이력은 아니다.
- 전체/XXXA/B/C 각각 `간트 · 표시 공정` 데이터셋을 두고 기존 간트 카드를 연결한다.
  재실행해도 사용자가 편집한 표시 SQL은 덮어쓰지 않는다.
  기본 lot_plan 데이터셋은 모든 계획 행을 유지한다.
- 앱의 원천 연결은 계속 읽기 전용이다. 확장 스크립트만 정확히 식별된 로컬 합성
  fixture에 쓰며 사내 DB의 구조나 데이터를 변경하지 않는다.
