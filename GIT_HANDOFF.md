# Git 전달 준비와 업로드

준비일: 2026-10-26. 현재 목적은 사내 PC의 1차 로컬 시연이다.

## 이번 준비 결과

- 로컬 `main` 저장소의 624개 파일을 최초 업로드 대상으로 검사했다. 커밋·업로드 상태는 `git log -1`과 원격 저장소에서 확인한다.
- 업로드 후보와 스테이징된 파일을 각각 검사했다. 현재 PC의 실제 설정·비밀번호·암호화 키와 일치하는 값, API 제공자 토큰·개인 키·JWT 패턴은 발견되지 않았다.
- 자격증명/DB URL 패턴에 걸린 23건은 검토했다. 테스트의 실패·정보 노출 방지·인코딩 검사에 쓰는 가짜 값, workspace 표시 문자열, 설치 시 변수를 이용해 만드는 연결 URL이었다. 실제 접속 정보로 확인된 항목은 없었다.
- 바이너리는 확인한 합성 화면 2개만 포함했다. 원시 파일·DB·키·실행 산출물은 제외 목록과 실제 스테이징 목록을 대조한다.
- Git 공백 검사에서 발견한 8개 파일의 줄 끝 공백·마지막 빈 줄만 정리했다. 공백 외 바이트는 동일하다. 앱 기능 변경이 없어 기능 검사를 다시 실행하지 않았다.
- 검사 세부 로그는 로컬 `.local/git-candidate-audit.json`, `.local/git-staged-audit.json`에 있으며 실제 비밀값은 기록하지 않는다. 이 로컬 파일들은 Git 대상이 아니다.

## 포함 범위

- 백엔드·FE 소스와 테스트, 의존성 정의/잠금 파일, 빈 설정 예제 `.env.example`.
- 로컬 실행·Parquet 내보내기/반입 스크립트와 합성 데모 생성기. 생성된 DB·측정 파일 자체는 포함하지 않는다.
- `COMPANY_DEMO_GUIDE.md`, `CLAUDE.md`, `AGENTS.md`, `COMPANY_HANDOFF.md`, `FRONTEND_GUIDE.md` 및 기능·검증 문서.
- 직접 생성한 합성 데이터임을 확인한 FE 참고 이미지 `validation/company-demo-ui.png`, `validation/company-demo-card.png`.

## 제외 범위

`.env` 및 변형 파일, `.local` 전체(실제 DB·반입 파일·비밀번호·암호화 키·검증 상태), 가상환경과 패키지 폴더, 빌드 결과와 이전 `dist-*` 폴더, 로그/캐시를 제외한다. 데이터 파일·DB 복사본·키 파일·압축 파일도 `.gitignore`에서 제외한다.

검증 중 저장한 JSON/텍스트/이미지와 과거 설계 HTML은 업로드 대상에서 뺐다. 파일은 로컬에 그대로 있다. 과거 Markdown이 가리키는 실행 결과 일부는 새 clone에 없으며 [validation/README.md](validation/README.md)에 설명했다.

검사에는 실제 로컬 설정과 키의 값이 후보 파일에 다시 들어 있는지 대조하는 검사, API 토큰·개인 키·JWT·자격증명 문자열·DB URL 패턴 검사를 사용한다. 값은 출력하지 않는다. 패턴에 걸린 테스트용 가짜 자격증명과 workspace 표시 문자열은 코드의 사용 목적을 확인해 구분한다. 바이너리 파일은 자동 텍스트 검사 대상이 아니므로 포함한 두 합성 이미지는 직접 확인한다.

`.gitignore`만으로 이미 추적 중인 파일이나 `git add -f`를 막을 수는 없다. 새 데이터나 실제 설정을 추가한 뒤에는 **스테이징된 내용**을 다시 점검한다. 검사 결과는 이번 스냅샷 범위이며 미래 변경까지 보장하지 않는다.

## 업로드할 때

로컬 브랜치는 `main`이다. 사용자가 지정한 최초 업로드 대상은 [comkdy/project-manager](https://github.com/comkdy/project-manager)이며, 2026-10-26 확인 시 공개(public) 저장소다. 검사한 코드·문서·합성 참고 화면만 전달하며 실제 회사 데이터·설정·키는 포함하지 않는다. 회사에서의 사용·추가 업로드에는 사내 정책에 맞는 Git 서비스와 공개 범위를 적용한다.

프로젝트 루트에서 파일 목록과 변경 요약을 검토한다.

```powershell
git status --short
git diff --cached --stat
git diff --cached --name-only
git check-ignore .env .local/runtime.json .local/db-credentials.key .local/llm-credentials.key
```

작성자 정보가 필요한 경우 본인의 Git 설정을 사용한다. 아래 명령은 검토 후 직접 실행하거나 후속 작업으로 요청한다. URL은 실제 허용된 저장소 주소로 바꾼다.

```powershell
git commit -m "Prepare local company demo and handoff guides"
git remote add origin "<허용된 비공개 저장소 URL>"
git push -u origin main
```

이미 원격이 등록되어 있다면 기존 연결을 먼저 확인하고 중복 추가하거나 임의로 바꾸지 않는다. 토큰이나 비밀번호를 원격 URL에 넣지 않고 회사의 인증 방식을 사용한다.

## 회사에서 받으면

프로젝트를 clone한 뒤 `CLAUDE.md`와 [시연 지침](COMPANY_DEMO_GUIDE.md)부터 읽는다. 코드만 받아서는 현재 PC의 DB·카드·지식·대화·모델 키까지 복원되지 않는다. 회사 Parquet는 승인된 사내 전달 방식으로 `.local/imports/export_01/`에 별도 준비하고, 새 PC의 실행 환경과 키는 해당 환경에서 설정한다.

이 준비 작업은 앱 기능을 변경하지 않는다. 기존 기능 검증 기록은 [company-demo-readiness.md](validation/company-demo-readiness.md)에 있으며 Git 준비 과정의 파일 검사는 기능 테스트와 구분한다.
