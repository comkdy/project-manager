# 카드 구성 Skill · v1

사용자의 비교·추이·분포·상관관계·요약 목적에 맞는 CardSpec을 구성한다.
한국어 explanation에 차트 선택과 집계 방식의 이유를 간결히 설명한다.

- supplied datasets의 id/version과 실제 columns만 참조한다.
- 기존 데이터셋으로 요청을 충족할 수 있으면 재사용한다. 적합한 것이 없으면
  needs_dataset을 반환하고 query_request에 필요한 조회 조건·컬럼·집계 단위를 적는다.
  이후 쿼리 작성 → 데이터셋 생성 후 다시 이 Skill을 호출한다.
- draft_dataset이 제공된 경우 그 데이터셋의 id/version을 그대로 사용한다.
  반복해서 needs_dataset을 반환하지 말고 모호하거나 불가능하면 clarification을 반환한다.
- CardSpec으로 차트를 구성한다. 사용자 정의 차트는 함께 제공된 Python 카드 지침을 따른다.
  임의 JavaScript/HTML 또는 차트 외 목적의 Python은 생성하지 않는다.
- 추이는 line, 범주 비교는 bar, 분포는 histogram/box, 상관관계는 scatter,
  요약 수치는 kpi를 고려한다. 지원 목록 내에서 사용자 의도를 우선한다.
- 이미 계산된 비율·합계에 불필요한 AVG/SUM을 적용하지 않는다. bar/line의 집계 완료
  데이터는 aggregate=none을 사용할 수 있다. KPI는 제공된 정의에 맞는 집계를 선택한다.
- yield는 tested_dies/passed_dies가 있을 때만 사용한다. 임의 수율 정의를 가정하지 않는다.
- 수정은 selected_card.id를 유지한다. 새 카드의 실제 ID는 서버가 부여한다.
- 렌더러는 기본 plotly. 저장됐다고 주장하지 않는다.
- 랏 공정 일정은 gantt를 사용하며 gantt.data_mode="dataset"으로 구성한다.
  조인 SQL 데이터셋의 출력에는 lot_cd, lot_id, operation_seq, operation, current_operation,
  current_started_at, remark, completed_at, history_count, expected_hours, tat_samples,
  tat_basis가 필요하다. 없으면 needs_dataset으로 계획·현재 상태·이력·TAT를 조인하는
  조회를 요청한다. 원천 이름을 추정하지 말고 실제 catalog를 사용한다.
  조인·skip 제외·TAT 집계는 수정 가능한 데이터셋 SQL에 둔다. 숨겨진 공정도 시간 계산에
  필요하므로 주요 공정 조건으로 데이터셋의 경로를 잘라내지 않는다.
  시작선은 gantt.line_filters, 깃발과 공정명은 gantt.key_filters로 독립 설정한다.
  각 필터 목록은 AND이며 빈 line_filters는 전체 선, 빈 key_filters는 깃발 없음이다.
  필터는 실제 데이터셋 출력 컬럼을 참조한다. 어떤 공정이 핵심인지는 사용자 지시에 따른다.
  aggregate=none, 축·계산식·trellis 없이 구성한다. TAT 단위는 hour다.
  현재 공정 시작 시각이 없으면 이전 완료 시각부터의 대기 포함 추정 구간임을 설명한다.
  시작을 알 수 없거나 기준 TAT를 초과하면 미래 시점을 임의로 만들어 내지 않는다.
  기간 설정은 표시 범위에만 적용하고 완료 이력을 잘라내지 않는다.
- 기간 필터는 기본적으로 대시보드 공통 기간을 따른다(period.use_dashboard=true).
  context.period_start가 실제 미리보기와 대시보드에 동일하게 적용된다.
  JOIN에서 날짜 컬럼이 여러 원천에 있으면 어떤 측정 시각이 조회 기준인지 확인한다.
  요청에서 명확하면 period.source_id와 period.column을 명시하고 explanation에 기준을 적는다.
  기준이 모호하면 clarification으로 질문한다. 앞선 공정과 후속 공정의 측정 시각은 다를 수 있다.
  사용자가 공통 기간 해제를 요청하면 period.use_dashboard=false로 설정한다.
  빈 미리보기를 채우기 위해 임의로 기간을 해제하거나 SQL 조건을 넓히지 않는다.
  개별 기간은 사용자가 요청한 일반 필터 또는 SQL 조건으로 작성한다. 공통 기간 조건은
  대시보드 조회 시 원천 날짜·시간 컬럼에 집계 전에 적용되므로 카드용 쿼리에
  임의의 최근 기간 조건을 중복 작성하지 않는다. 히트맵은 지원하지 않는다.

출력은 제공된 JSON schema의 card, needs_dataset 또는 clarification이다.
preview_card Tool이 실제 데이터로 검증하고 apply_proposal Tool이 사용자 적용을 저장한다.
