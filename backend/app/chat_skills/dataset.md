# 데이터셋 생성 Skill · v1

검증된 query를 앱에서 재사용할 데이터셋 초안으로 구성한다.
원천 DB에 테이블을 생성하는 작업이 아니라 앱의 조회 정의를 만드는 작업이다.

- 사용자 목적과 후속 카드 요구사항에 맞는 간결한 한국어 name을 정한다.
- 입력 validated_query의 SQL, source_id, 컬럼, pivot 설정을 변경하거나 재작성하지 않는다.
  데이터셋 spec은 prepare_dataset Tool이 검증된 query에서 구성한다.
- 입력 컬럼과 집계 단위를 보고 어떤 데이터셋인지 explanation에 설명한다.
- 프로젝트 범위는 서버가 결정한다. DB 접속 정보, 저장 위치, 임의 버전을 만들지 않는다.
- 기존 데이터셋을 자동 덮어쓰지 않는다. 초안은 적용 전까지 저장되지 않는다.

출력은 제공된 JSON schema의 dataset 또는 clarification이다.
prepare_dataset Tool이 초안을 만들고, 사용자 적용 시 apply_proposal Tool이 저장한다.
