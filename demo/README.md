# Wafer demo data

사내 컬럼 구성에 맞춘 최신 테스트 DB는 **`wafer_fab_demo`**입니다.
6개 `lot_cd` 파티션 테이블, lot당 10~15장 wafer 및 연결된 Inline/EPM/이력 데이터의
생성·조회·검증 방법은 [사내 스키마 합성 데이터](fab/README.md)를 참고하세요.
아래 기존 `wafer_demo`는 이전 화면과 회귀 테스트를 위해 보존합니다.

The fixtures are a synthetic semiconductor dataset, not production specifications.
`schema.sql` owns seven source tables and two read-only views. Three projects share
process tools; project/lot/wafer identifiers are copied onto measurements so that
all dashboards can filter consistently without raw fact-to-fact JOINs.

## Generate and verify

From the repository root in PowerShell, after installing backend dependencies:

```powershell
.\scripts\setup-database.ps1
.\scripts\seed-demo.ps1 -Profile dev
.\.venv\Scripts\python.exe .\demo\verify.py --profile dev
```

The setup downloads PostgreSQL 17.11-3 from EDB, checks its published SHA-256, and
extracts the server runtime inside `.local/pgsql`. It creates a loopback-only
cluster at `.local/pgdata`, port 55432, without an installer, Windows service, or
changes to another PostgreSQL installation. Launchers use hidden windows.

Initial web credentials are written to ignored `.local/demo-accounts.json`;
database credentials are written to ignored `.env` and `.local/runtime.json`.
No credentials are printed. Keep these files private. `wafer_source` can read
only the demo source database; `wafer_metadata` owns only the app database.

```powershell
.\scripts\stop-database.ps1
.\scripts\start-database.ps1
```

To replace fixtures with the large profile, use the explicit `-Replace` option.
The operation changes only this workspace's identified `wafer_demo` database:

```powershell
.\scripts\seed-demo.ps1 -Profile perf -Replace
.\.venv\Scripts\python.exe .\demo\verify.py --profile perf
# Restore the small development dataset when finished:
.\scripts\seed-demo.ps1 -Profile dev -Replace
```

`seed.py` deliberately has no connection-URL option and never reads the app's
source connection setting. It checks the runtime's exact workspace directory,
fixed loopback port, database name and database identity marker before writing.
Replacement is a transaction; it never modifies app metadata.

## Sizes and reproducibility

| Profile | Projects | Lots | Wafers | Inline rows | WAT rows | EDS die rows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | 3 | 6 | 150 | 15,000 | 2,700 | 106,350 |
| perf | 3 | 60 | 1,500 | 2,100,000 | 27,000 | 1,063,500 |

Each Lot contains 25 wafers. Development fixtures use the first six Lots and the
first 100 inline measurement positions per wafer; performance fixtures use the
first 60 Lots and 1,400 positions. Every development fact is also present, with
identical ID and values, in the large profile for the same seed. Profiles and
COPY batch sizes do not enter the value-generation function. Default seed is
`20261019`; `--batch-size` controls only the bounded COPY buffer, default 5,000.

Known anomalies include Lot `P01-202610-0002` (ID 4) with higher film thickness and
lower EDS yield, tool 3 thickness offset, slow time drift and higher edge failure
probability. NULL inline values and untested dies exercise missing-value paths.
Manifests contain exact expected counts and independently generated per-project
and per-Lot tested/PASS totals under `.local/manifest-{profile}.json`.

`wafer_summary` and `wafer_correlation` have one row per wafer. Their measurements
are aggregated separately before joining: inline `thickness` at `DEP`, WAT `VTH`
at test run 1, and EDS test run 1. Yield is `100 * passed / tested`; untested dies
are excluded. Aggregate project/Lot yield must sum the counts, not average the
wafer percentages. Larger profiles add measurement sites to the same wafers, so
derived wafer means can change even though shared raw facts remain identical.

## Checks and evidence

`python -m unittest discover -s demo -p 'test_*.py'` runs pure generator checks.
`verify.py` checks SQL row counts, weighted yields, summary grain, deterministic
values including NULLs, and database-role isolation. It records representative
query timings and `EXPLAIN (ANALYZE, BUFFERS)` output under
`.local/verification-{profile}.json`. The application result cache is not used;
PostgreSQL's buffer cache may be warm. These are local measurements, not a
guarantee for production hardware or schemas.

The initial Windows run on 2026-10-19 loaded all 3,190,500 performance-profile
facts in 145.5 seconds. The generator's peak working set was 170.1 MiB. Seven
representative SQL queries took 0.5–413 ms, including weighted project/Lot yield,
equipment distribution, wafer correlation, wafer map and keyset pages. This
measures SQL and fixture generation; API/UI timings are recorded separately.

Binary provenance:
[PostgreSQL Windows downloads](https://www.postgresql.org/download/windows/),
[EDB binary distribution](https://www.enterprisedb.com/download-postgresql-binaries),
[EDB-published archive checksum](https://github.com/EnterpriseDB/edb-installers/issues/682).
