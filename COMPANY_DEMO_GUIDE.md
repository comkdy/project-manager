# 사내 시연 준비 지침

작성일: 2026-10-26. 대상: 회사 Windows PC에서 이 프로젝트를 처음 실행하는 사용자 또는 Claude Code.

개발 보조 AI에게 먼저 [CLAUDE.md](CLAUDE.md), [AGENTS.md](AGENTS.md), [시스템 인계](COMPANY_HANDOFF.md)를 읽게 하세요. FE를 수정할 때는 [FE 설계·디자인 지침](FRONTEND_GUIDE.md)을 추가로 읽습니다. 현재는 PC 로컬 시연을 준비하며 Docker·CI/CD 전환은 보류합니다.

## 권장 구성

사내 PostgreSQL 서버에 접속하지 않고, 시연 PC 안에서 PostgreSQL을 실행합니다. 기존 앱의 SQL·통계·ML 기능이 PostgreSQL을 사용하므로 시연 준비 단계에서는 DB 엔진을 바꾸지 않습니다.

```text
사내 S3 / StarRocks
  → 사내 전용 모듈로 Spark DataFrame 조회
  → 필요한 범위만 Parquet 등의 파일로 저장
  → 로컬 PostgreSQL의 별도 임시 분석 DB에 적재
  → 웹 앱에서 DB 등록·활성화
```

로컬 PostgreSQL 실행, Parquet 저장 예제, 파일 반입·DB 등록 스크립트가 준비되어 있습니다. 사내 모듈의 조회 호출과 회사 모델의 접속 설정은 사내 환경에서 연결해야 합니다. 검증 범위와 결과는 [시연 경로 검증 기록](validation/company-demo-readiness.md)을 참고하세요.

- **앱 관리 DB**: 사용자, 프로젝트, 데이터셋 정의, 카드, AI 지식, 채팅, 확인 요청 등을 저장합니다. `APP_DATABASE_URL`로 연결합니다.
- **분석 DB**: `wafer_his`, Inline, EPM 등 분석할 데이터를 저장합니다. 웹의 **워크스페이스 관리 → DB 설정**에서 등록합니다.
- 합성 데모 데이터와 회사 데이터는 서로 다른 분석 DB로 구분합니다. 기존 데모 DB를 회사 데이터로 덮어쓰지 않습니다.

## Git으로 전달되는 범위

소스 코드, 스크립트, 문서, `.env.example`, 패키지 정의·잠금 파일을 전달합니다. Git clone만으로 현재 PC의 데이터와 설정까지 복제되지는 않습니다.

현재 [.gitignore](.gitignore)는 `.env`와 변형 파일, `.local/`, `.venv/`, `node_modules/`, `dist/` 및 이전 `dist-*`, 로그와 주요 데이터·키 파일을 제외합니다. 전달 범위와 업로드 순서는 [GIT_HANDOFF.md](GIT_HANDOFF.md)를 참고하세요.

- PostgreSQL 실행 파일·설치 ZIP·DB 파일·암호화 키는 `.local/`에 있으며 Git에 포함되지 않습니다.
- 회사에서 추출한 파일은 `.local/imports/`처럼 제외된 경로에 둡니다. CSV·Parquet 등도 확장자로 제외하지만, 다른 형식이나 이미 Git에서 추적하는 파일까지 안전하다고 가정하지 말고 올릴 목록을 확인합니다.
- 접속 비밀번호, API 키, 실제 회사 데이터는 Git에 넣지 않습니다. `.env.example`에는 빈 설정 항목만 유지합니다.
- 새 PC에서 새 관리 DB를 생성하면 기존 PC의 평가 사례·채팅·카드 설정은 따라오지 않습니다. 이를 유지하려면 별도의 관리 DB 이전과 대응하는 암호화 키 이전이 필요합니다.
- 다른 PC의 `.venv`나 `.local`을 그대로 복사해 실행된다고 가정하지 않습니다. Python 환경과 DB 실행 정보에 기존 PC의 절대 경로가 들어 있습니다.

## 새 Windows PC에서 최초 준비

아래 명령은 **새로 받은 프로젝트의 루트 폴더**에서 PowerShell로 실행합니다. 기존 실행 환경이 있다면 먼저 확인하고 재생성하지 않습니다.

Python 3.11 이상이 필요하며 현재 프로젝트는 Python 3.13에서 검증했습니다. 프런트엔드를 빌드하려면 Node.js와 pnpm도 필요합니다. PostgreSQL 스크립트는 Windows x64용이며 서비스 등록이나 Docker 없이 실행합니다. 회사 PC에서 해당 프로그램 실행이 허용되어야 합니다.

### 1. Python 환경 준비

`uv`가 설치되어 있는 경우:

```powershell
uv venv .venv --python 3.13
uv pip install --python .venv\Scripts\python.exe -r .\backend\requirements.lock -e .\backend
```

`uv`가 없고 Python이 설치되어 있는 경우:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.lock -e .\backend
```

### 2. 로컬 PostgreSQL 준비

```powershell
.\scripts\setup-database.ps1
```

이 스크립트는 PostgreSQL 실행 파일을 `.local/pgsql`에 준비하고, `.local/pgdata`에 DB를 생성한 뒤 `127.0.0.1:55432`에서 실행합니다. 앱 관리 DB 연결도 `.env`에 구성합니다. 기존 `.env`의 연결 설정을 변경하므로 이미 구성된 PC에서는 내용을 확인한 뒤 사용합니다.

시연용 `.env`에서 아래 값을 확인합니다. 초기 템플릿의 `AUTH_MODE=sso`가 남을 수 있으므로 반드시 확인합니다.

```dotenv
DEMO_MODE=true
AUTH_MODE=local_admin
```

이 구성은 시연 PC의 로컬 접속용입니다. 이 상태에서 사내 다른 PC에 공개하도록 서버 주소를 변경하지 않습니다.

### 3. 앱 실행

```powershell
.\scripts\start-app.ps1
```

앱 주소는 `http://127.0.0.1:8000`입니다. 스크립트가 필요한 프런트엔드 패키지를 설치하고 빌드합니다. `frontend/dist/index.html`을 포함한 빌드 결과 전체를 이미 준비했다면 `-NoBuild`를 사용할 수 있습니다.

합성 데이터로 먼저 화면을 확인하려는 **새 시연 환경**에서는 앱을 실행하기 전에 다음 명령을 선택적으로 실행합니다. 회사 데이터 반입용 명령은 아닙니다.

```powershell
.\scripts\seed-demo.ps1 -Profile dev
```

기존 데이터 교체 옵션인 `-Replace`는 시연 준비 과정에서 사용하지 않습니다.

## 회사에서 다운로드가 어려운 경우

- PostgreSQL ZIP은 회사에서 허용하는 전달 방식으로 `.local/postgresql.zip`에 준비합니다. 현재 스크립트가 지정한 버전과 SHA-256 검증값에 맞는 파일이어야 합니다.
- 다음 명령으로 추가 다운로드 없이 PostgreSQL을 준비합니다.

```powershell
.\scripts\setup-database.ps1 -SkipDownload
```

- Python 실행 환경과 백엔드 의존성도 사내 패키지 저장소 또는 미리 준비한 설치 파일·패키지로 설치해야 합니다. PostgreSQL ZIP 하나만으로 앱 전체를 설치할 수는 없습니다.
- Node.js·pnpm 설치와 패키지 다운로드가 어렵다면 검증된 `frontend/dist/` 전체를 별도로 전달하고 `start-app.ps1 -NoBuild`를 사용합니다. 빌드 결과는 현재 Git 제외 대상입니다.
- 회사 정책으로 실행·설치가 차단되면 차단 내용을 확인하고 허용된 배포 방식으로 준비합니다.

## 사내 데이터 연결 준비

사내 모듈에서 시연할 프로젝트·기간을 한정하여 추출합니다. Spark가 저장한 Parquet는 여러 파일로 구성된 폴더일 수 있으므로 모든 데이터 조각을 함께 전달합니다. `lot_cd`는 기존 프로젝트 구분 컬럼이므로 이름과 값을 그대로 보존합니다. 나머지 컬럼명도 데모 이름으로 바꾸지 않습니다.

세 폴더를 `.local/imports/export_01/{wafer_his,inline,epm}/`에 준비한 뒤, 로컬 DB 준비와 Python 패키지 설치를 완료한 프로젝트 루트에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts/import_parquet_demo.py --input .local/imports/export_01 --database wafer_company_demo --activate
```

이 명령은 **새 DB만 생성**하며, 전체 Parquet 조각을 묶어서 적재하고 행 수를 확인합니다. 원래 컬럼명·값·`lot_cd`를 유지합니다. 앱에는 읽기 전용 계정으로 등록하며 `--activate`를 지정하면 활성 DB까지 전환합니다. `--activate`를 생략하면 등록만 하고 기존 활성 DB를 유지합니다.

실제 데이터는 새 DB의 `import_data` 스키마에 저장하고, 앱에는 같은 컬럼을 가진 `public.wafer_his`, `public.inline`, `public.epm` 읽기 전용 뷰를 제공합니다. 뷰는 원본 행을 JOIN·집계·변환하지 않습니다. 앱 화면에 원본 저장 테이블을 중복으로 노출하지 않습니다.

기존 DB 이름으로 재실행하면 중단합니다. 실패 시 자동으로 기존 DB를 지우지 않습니다. 파일 적재 후 앱 등록만 실패했다면 설정을 고친 뒤 다음 명령으로 다시 등록합니다.

```powershell
.\.venv\Scripts\python.exe scripts/import_parquet_demo.py --database wafer_company_demo --register-only --activate
```

반입 완료 출력의 `registered: true`, `active: true`를 확인하고 앱을 새로고침합니다. DB 설정에서 **연결됨·활성**과 세 데이터가 보이는지 확인합니다. 프로젝트 관리에서 실제 `lot_cd` 값을 연결하면 해당 프로젝트의 데이터셋이 생성됩니다. 다른 DB에 있던 프로젝트·카드·지식이 새 DB에 자동 복제되지는 않습니다.

반입 지원 범위는 문자열·정수·실수·소수·불리언·날짜·시각입니다. Spark의 INT96 시각도 마이크로초 정밀도를 보존할 수 있으면 읽습니다. 나노초 손실, 배열·구조체·바이너리·타입 미정 컬럼, 파일 간 스키마 차이, 100개 초과 컬럼, 파티션 컬럼이 경로에만 있는 저장 방식은 임의 변환하지 않고 중단합니다. 제공한 저장 예제처럼 평평한 part 파일 폴더로 저장하고 필요한 컬럼을 선택하세요. 시각의 업무상 시간대와 단위는 AI 지식으로 별도 확인합니다.

다음 정보는 원본의 의미와 타입을 유지해야 합니다.

- 공정 이력: operation, recipe, eqp_id, 공정 시각, Lot·Wafer 등 연결 키.
- Inline: **operation + parameter를 하나의 측정 항목으로 취급**, 측정값·단위·시각 및 연결 키.
- EPM: parameter, 측정값·단위·시각 및 연결 키.
- 프로젝트 구분, 반복 측정 구분, 누락값, 시간대, 숫자 정밀도. 선행 0이 있는 식별자를 임의로 숫자로 바꾸지 않습니다.

실제 컬럼명이 다르면 확인한 매핑을 사용합니다. 없는 장비값·공정·연결 키를 추정하여 생성하지 않습니다.

적재용 계정과 앱 조회용 계정을 구분합니다. 앱에 등록할 원천 계정은 필요한 테이블을 조회할 수 있는 읽기 전용 계정이어야 합니다. DB 등록·권한·프로젝트 연결 절차는 [README의 실제 PostgreSQL 연결](README.md#실제-postgresql-연결)을 참고합니다.

새 DB의 프로젝트 소속과 테이블 관계는 컬럼 이름만으로 자동 확정되지 않습니다. 프로젝트 조건과 관계를 확인한 뒤 데이터셋·카드를 구성합니다.

### Spark DataFrame을 파일로 저장하는 예제

재사용할 수 있는 Python 예제는 [scripts/export_spark_demo.py](scripts/export_spark_demo.py)에 있습니다. 사내 모듈이 실행되는 Python/Spark 환경에서 `export_demo`를 import한 뒤 조회한 DataFrame 세 개와 저장 경로를 전달합니다. 회사 모듈 호출과 PostgreSQL 반입은 포함하지 않습니다. 실제 사내 Spark 환경에서의 실행은 별도로 확인해야 합니다.

Parquet를 기본 형식으로 사용합니다. 숫자·문자·날짜 등 컬럼의 타입 정보를 파일에 담을 수 있습니다. 다만 키 제약, 업무상 단위, 시간대 해석 등은 별도로 기록해야 합니다. [Spark 공식 Parquet 안내](https://spark.apache.org/docs/latest/sql-data-sources-parquet.html)

아래 코드는 사내 모듈이 반환한 배치 Spark DataFrame을 각각 `wafer_his_df`, `inline_df`, `epm_df`라는 변수로 받아 둔 상태를 가정합니다. 모듈 호출 자체는 회사의 실제 사용법을 따릅니다.

```python
# 예시 경로입니다. 실제 쓰기 권한이 있는 저장 경로로 바꿉니다.
# s3a://는 현재 Spark 환경에 해당 연결 설정이 갖춰진 경우 사용합니다.
export_root = "s3a://YOUR_EXPORT_BUCKET/project-manager/demo/export_01"

frames = {
    "wafer_his": wafer_his_df,
    "inline": inline_df,
    "epm": epm_df,
}

for name, frame in frames.items():
    (
        frame.write
        .mode("errorifexists")
        .option("compression", "snappy")
        .parquet(f"{export_root}/{name}")
    )
```

기존 결과를 덮어쓰지 않도록 `errorifexists`를 사용했습니다. 다시 추출할 때는 `export_02`처럼 새 경로를 사용합니다. 세 데이터의 저장이 모두 성공한 뒤 전달합니다. [저장 API](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.parquet.html)

저장 경로는 Spark 실행 위치에 맞춰 선택합니다.

| 실행 위치 | 저장 경로 |
| --- | --- |
| 회사 PC에서 Spark를 로컬 모드로 실행 | 예: `file:///C:/demo-export/export_01` |
| 사내 서버·클러스터 또는 원격 Spark | Spark 작업 노드가 함께 접근 가능한 S3·HDFS·공유 저장소 |

원격 Spark에서 `C:/...`를 지정해도 사용자의 회사 PC로 바로 다운로드되는 것은 아닙니다. S3를 읽을 수 있다는 것과 별도 경로에 쓸 수 있다는 것도 다르므로, 추출용으로 쓰기 가능한 경로를 사용합니다.

보통 다음과 같이 데이터별 폴더 안에 여러 파일이 생깁니다. 파일 수와 이름은 실행마다 달라질 수 있습니다.

```text
export_01/
  wafer_his/
    part-00000-....snappy.parquet
    part-00001-....snappy.parquet
  inline/
    part-00000-....snappy.parquet
  epm/
    part-00000-....snappy.parquet
```

사내 저장소의 다운로드 기능이나 승인된 파일 전달 도구로 `export_01` 아래 파일을 전부 내려받고 폴더 구조를 유지합니다. PC에서는 프로젝트의 `.local/imports/export_01/`에 두면 됩니다. `part-00000` 하나만 가져오면 데이터 일부만 가져올 수 있습니다. 여러 파일은 반입기가 하나의 테이블 데이터로 읽습니다.

필요하면 저장된 결과를 다시 읽어 행 수와 컬럼 구조를 확인합니다. `count()`는 전체 저장 데이터를 읽는 작업이므로 대량 데이터에서는 비용을 고려합니다. 원시 행을 대화에 출력할 필요는 없습니다.

```python
saved = spark.read.parquet(f"{export_root}/wafer_his")
print("저장된 행 수:", saved.count())
saved.printSchema()
```

추출할 때는 같은 프로젝트·Lot/Wafer 집합을 연결할 수 있도록 범위를 맞춥니다. 세 테이블을 각각 임의의 행 수로 잘라내면 서로 대응하는 데이터가 사라질 수 있습니다. 공정 이력은 Inline·EPM보다 앞선 관련 공정까지 포함해야 합니다. 원본을 미리 JOIN·pivot할 필요는 없으며, Inline의 operation과 parameter를 모두 보존합니다.

전체 데이터를 `toPandas()`로 모으는 방식은 기본 경로로 사용하지 않습니다. 모든 데이터가 드라이버 메모리에 올라가므로 충분히 작은 결과에만 적합합니다. 파일 하나를 만들려고 무조건 `coalesce(1)`을 사용하는 것도 피합니다. [toPandas 설명](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.toPandas.html), [coalesce 설명](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.coalesce.html)

## 시연 확인 항목

1. `/api/health`와 앱 첫 화면이 정상적으로 열리는지 확인합니다.
2. 임시 분석 DB를 등록하고 활성 DB·테이블 목록을 확인합니다. 합성 DB와 데이터가 섞이지 않는지 확인합니다.
3. 프로젝트별 조회 조건, 데이터셋 작성·수정, 카드 편집·저장을 확인합니다.
   - 과거 기간의 스냅샷을 반입했다면 대시보드의 조회 시작 주차를 해당 데이터 기간에 맞춥니다. 기본 최근 기간 밖의 데이터는 차트에 표시되지 않습니다.
4. AI 지식에 적용 범위와 자연어 평가 사례를 등록하고, 모호한 용어가 확인 요청으로 이어지는지 확인합니다.
5. 분석 결과 검토·승인과 승인 지식의 채팅 활용을 확인합니다. 관측된 연관성을 인과효과로 단정하지 않는지 확인합니다.
6. AI 채팅과 평가 사례 자동 정리는 모델 연결이 별도로 필요합니다. 사내에서 허용한 모델 주소·모델명·인증 방식이 앱과 호환되는지 확인합니다. 사내 Claude 채팅을 사용할 수 있다는 것만으로 앱의 모델 연결이 완료되지는 않습니다.
7. 원시 측정 행은 외부 모델로 보내지 않습니다. 회사 데이터 연결 전에 실제 전송 경로와 요청 내용을 점검하고, 허용된 정보만 사용합니다.

## 다음 실행과 종료

최초 준비와 빌드가 완료되었다면:

```powershell
.\scripts\start-app.ps1 -NoBuild
```

앱 실행 스크립트가 로컬 DB도 시작합니다. 종료할 때는:

```powershell
.\scripts\stop-app.ps1
.\scripts\stop-database.ps1
```

실행 오류는 `.local/app.stderr.log`와 `.local/postgresql.log`에서 확인합니다. 로그나 `.env`를 대화에 통째로 출력하지 말고 접속 정보와 데이터가 포함되지 않은 필요한 오류 부분만 확인합니다.

## 사내 Claude에 전달할 지시문

아래 내용은 프로젝트 폴더와 터미널에 접근할 수 있는 Claude Code 등의 환경에 전달합니다. 일반 채팅 환경에서는 실행 안내를 받아 사용자가 직접 실행해야 합니다.

> 이 프로젝트를 회사 Windows PC에서 로컬 시연할 수 있도록 준비해줘.
>
> 먼저 CLAUDE.md, AGENTS.md, COMPANY_HANDOFF.md, COMPANY_DEMO_GUIDE.md와 실행 스크립트를 읽어줘. FE를 수정할 때는 FRONTEND_GUIDE.md도 읽고 기존 화면의 디자인·상태·저장 흐름을 유지해줘. 현재 목표는 PC 로컬 시연이며 Docker·CI/CD 전환은 하지 않아.
>
> 사내 PostgreSQL 서버에는 접속할 수 없으므로 이 PC에서 로컬 PostgreSQL을 실행하는 방식을 먼저 확인해줘. Python과 프런트엔드 실행 환경을 준비하고 기존 스크립트를 활용해줘. 기존 설정이나 데이터는 보존하고, 다른 PC에서 복사한 실행 환경이나 DB 경로를 그대로 사용할 수 있다고 가정하지 말아줘.
>
> 다운로드가 막혀 있다면 필요한 파일·패키지를 구체적으로 알려줘. PostgreSQL ZIP을 준비한 경우 setup-database.ps1 -SkipDownload를 사용할 수 있어. 로컬 시연 설정은 DEMO_MODE=true, AUTH_MODE=local_admin인지 확인해줘.
>
> 앱을 실행하고 프로젝트·데이터셋·카드 편집·AI 지식·확인 요청 화면을 확인해줘. AI 채팅과 사례 자동 정리는 사내에서 허용된 모델 연결을 별도로 점검해줘.
>
> 사내 모듈에서 추출한 Parquet는 .local/imports/export_01/ 아래 wafer_his, inline, epm 폴더에 준비할게. scripts/import_parquet_demo.py로 새 임시 분석 DB에 반입하고 --activate로 앱 등록·활성화까지 해줘. 기존 DB는 보존하고, lot_cd는 이름과 값을 그대로 유지해줘. 컬럼명을 데모에 맞추려고 임의로 바꾸지 말아줘. 실제 모듈과 데이터 구조는 내가 제공할게. Inline은 operation+parameter를 하나의 항목으로 다뤄줘.
>
> 원시 측정 행을 외부 모델로 보내지 말고, 비밀번호나 원시 측정 행을 대화에 출력하지 마. 분석 결과는 확인 요청에서 검토·승인한 뒤 지식으로 축적하는 기존 흐름을 유지해줘.
