"""Import a Parquet snapshot into a NEW managed local PostgreSQL database.

No source values/credentials are printed. Existing databases are never replaced.
All original columns, including lot_cd, are preserved without inferred aliases.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

import psycopg
from psycopg import sql
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("wafer_his", "inline", "epm")


class ImportProblem(ValueError):
    """Safe diagnostic containing metadata only, never measurement values."""


def identifier(name):
    if not isinstance(name, str) or not name or "\x00" in name or len(name.encode("utf-8")) > 63:
        raise ImportProblem("테이블·컬럼 이름은 비어 있지 않고 UTF-8 기준 63바이트 이하여야 합니다.")
    return sql.Identifier(name)


def postgres_type(t):
    if pa.types.is_dictionary(t):
        return postgres_type(t.value_type)
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_signed_integer(t):
        return "smallint" if t.bit_width <= 16 else "integer" if t.bit_width == 32 else "bigint"
    if pa.types.is_unsigned_integer(t):
        return {8: "smallint", 16: "integer", 32: "bigint", 64: "numeric(20,0)"}[t.bit_width]
    if pa.types.is_float32(t):
        return "real"
    if pa.types.is_float64(t):
        return "double precision"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "text"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_timestamp(t) and t.unit in ("s", "ms", "us", "ns"):
        return "timestamp with time zone" if t.tz else "timestamp without time zone"
    if pa.types.is_decimal(t):
        return f"numeric({t.precision},{t.scale})"
    raise ImportProblem(f"지원하지 않는 컬럼 타입: {t}. 임의 변환하지 않습니다. Spark에서 명시적으로 변환한 뒤 다시 추출하세요.")


@dataclass
class TableInput:
    name: str
    files: list[Path]
    schema: pa.Schema
    rows: int
    stamps: dict[Path, tuple[int, int]]


def stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def inspect_inputs(folder):
    folder = Path(folder).resolve(strict=True)
    tables = []
    for name in TABLES:
        directory = folder / name
        if not directory.is_dir():
            raise ImportProblem(f"{name} 폴더가 없습니다. 세 데이터 폴더를 모두 준비하세요.")
        files = sorted(directory.rglob("*.parquet"))
        if not files:
            raise ImportProblem(f"{name}: Parquet 파일이 없습니다.")
        schema, rows, stamps = None, 0, {}
        for path in files:
            if path.resolve().parent != directory.resolve():
                raise ImportProblem(f"{name}: 분할 경로의 컬럼 누락을 막기 위해 평평한 part 파일 폴더만 지원합니다. 제공한 export_demo 예제로 다시 저장하세요.")
            stamps[path] = stamp(path)
            with pq.ParquetFile(path) as part:
                current = part.schema_arrow
                if schema is not None and not schema.equals(current, check_metadata=False):
                    raise ImportProblem(f"{name}: 파일마다 컬럼이나 타입이 다릅니다. 서로 다른 추출 결과를 섞지 마세요.")
                schema = current
                rows += part.metadata.num_rows
        if not schema or len(schema.names) != len(set(schema.names)):
            raise ImportProblem(f"{name}: 비어 있거나 중복된 컬럼 구성이 있습니다.")
        if len(schema) > 100:
            raise ImportProblem(f"{name}: 앱의 기본 데이터셋 한도인 100개 이하로 필요한 컬럼을 선택하세요.")
        for field in schema:
            identifier(field.name)
            try:
                postgres_type(field.type)
            except ImportProblem as exc:
                raise ImportProblem(f"{name}.{field.name}: {exc}") from None
        if "lot_cd" in schema.names and postgres_type(schema.field("lot_cd").type) != "text":
            raise ImportProblem(f"{name}: lot_cd는 프로젝트 코드 문자열이어야 합니다. 임의로 변환하지 않습니다.")
        tables.append(TableInput(name, files, schema, rows, stamps))
    return tables


def managed_runtime():
    runtime = json.loads((ROOT / ".local/runtime.json").read_text(encoding="utf-8-sig"))
    if (runtime.get("marker") != "wafer-local-demo-v1" or runtime.get("port") != 55432
            or Path(runtime["data_dir"]).resolve() != (ROOT / ".local/pgdata").resolve()
            or Path(runtime["bin_dir"]).resolve() != (ROOT / ".local/pgsql/bin").resolve()):
        raise ImportProblem("현재 프로젝트의 로컬 PostgreSQL 설정이 아닙니다. setup-database.ps1을 확인하세요.")
    return runtime


def connect(runtime, database, *, reader=False, autocommit=False):
    return psycopg.connect(host="127.0.0.1", port=runtime["port"], dbname=database,
        user="wafer_source" if reader else "wafer_local_admin",
        password=runtime["source_password" if reader else "admin_password"],
        connect_timeout=5, autocommit=autocommit)


def copy_batch(batch):
    # Spark's legacy INT96 timestamps can be read as nanoseconds by Arrow.
    # PostgreSQL stores microseconds: safe casting rejects actual precision loss.
    target = pa.schema([pa.field(f.name, pa.timestamp("us", tz=f.type.tz), f.nullable)
        if pa.types.is_timestamp(f.type) and f.type.unit == "ns" else f for f in batch.schema])
    try:
        return batch.cast(target, safe=True)
    except pa.ArrowInvalid:
        raise ImportProblem("시각 정밀도를 PostgreSQL의 마이크로초 단위로 보존할 수 없습니다. 원천 정밀도를 확인하세요.") from None


def import_snapshot(folder, database):
    if not re.fullmatch(r"wafer_[a-z][a-z0-9_]{0,45}", database) or database in {"wafer_app", "wafer_demo", "wafer_fab_demo"}:
        raise ImportProblem("새 DB 이름은 wafer_로 시작하는 영문 소문자·숫자·밑줄이어야 합니다. 기존 앱·데모 DB는 사용할 수 없습니다.")
    tables = inspect_inputs(folder)
    runtime = managed_runtime()
    with connect(runtime, "postgres", autocommit=True) as connection:
        if connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
            raise ImportProblem("같은 이름의 DB가 이미 있습니다. 기존 DB를 보존하기 위해 새 이름을 지정하세요.")
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER wafer_local_admin").format(identifier(database)))
        connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(identifier(database)))
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO wafer_source").format(identifier(database)))
    report = {"database": database, "tables": [], "registered": False, "active": False}
    # All table creation/copy/verification is one transaction. Failure publishes no tables.
    with connect(runtime, database) as connection:
        connection.execute("SET TIME ZONE 'UTC'")
        connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE ON SCHEMA public TO wafer_source")
        connection.execute("CREATE SCHEMA import_data AUTHORIZATION wafer_local_admin")
        for table in tables:
            columns = sql.SQL(", ").join(sql.SQL("{} {}").format(identifier(f.name), sql.SQL(postgres_type(f.type))) for f in table.schema)
            raw = sql.Identifier("import_data", table.name)
            connection.execute(sql.SQL("CREATE TABLE {} ({})").format(raw, columns))
            fields = sql.SQL(", ").join(identifier(name) for name in table.schema.names)
            with connection.cursor().copy(sql.SQL("COPY {} ({}) FROM STDIN").format(raw, fields)) as writer:
                for path in table.files:
                    if stamp(path) != table.stamps[path]:
                        raise ImportProblem(f"{table.name}: 읽는 중 파일이 변경되었습니다.")
                    with pq.ParquetFile(path) as part:
                        for batch in part.iter_batches(batch_size=5000):
                            for row in copy_batch(batch).to_pylist():
                                writer.write_row(tuple(row[name] for name in table.schema.names))
                    if stamp(path) != table.stamps[path]:
                        raise ImportProblem(f"{table.name}: 읽는 중 파일이 변경되었습니다.")
            count = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(raw)).fetchone()[0]
            if count != table.rows:
                raise ImportProblem(f"{table.name}: 파일과 DB의 행 수가 일치하지 않습니다.")
            # Views expose original names; no project/measurement meaning is guessed.
            view = sql.Identifier("public", table.name)
            connection.execute(sql.SQL("CREATE VIEW {} AS SELECT * FROM {}").format(view, raw))
            connection.execute(sql.SQL("GRANT SELECT ON {} TO wafer_source").format(view))
            connection.execute(sql.SQL("ANALYZE {}").format(raw))
            report["tables"].append({"table": table.name, "rows": count, "files": len(table.files),
                "columns": [{"name": f.name, "type": postgres_type(f.type)} for f in table.schema]})
        connection.execute("CREATE TABLE import_data.snapshot_info (report jsonb NOT NULL)")
        connection.execute("INSERT INTO import_data.snapshot_info VALUES (%s::jsonb)", (json.dumps(report),))
    # Exercise exactly the same read-only policy as normal app source connections.
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    from app.source_policy import require_read_only_role
    url = URL.create("postgresql+psycopg", username="wafer_source", password=runtime["source_password"],
        host="127.0.0.1", port=runtime["port"], database=database)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            require_read_only_role(connection)
    finally:
        engine.dispose()
    return report


def register_snapshot(database, *, activate=False):
    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from app import db
    from app.auth import LOCAL_ADMIN_USERNAME
    from app.bootstrap import initialize
    from app.database_settings import ConnectionInput, load_active_source, save_or_activate
    from app.models import User
    runtime = managed_runtime()
    target = make_url(db.settings.app_database_url)
    if (not db.settings.demo_mode or db.settings.auth_mode != "local_admin" or target.host != "127.0.0.1"
            or target.port != runtime["port"] or target.username != "wafer_metadata"):
        raise ImportProblem("자동 등록은 현재 PC의 DEMO_MODE=true, AUTH_MODE=local_admin 환경에서만 사용하세요.")
    initialize()
    load_active_source()
    with db.SessionLocal() as session:
        actor = session.scalar(select(User).where(User.username == LOCAL_ADMIN_USERNAME))
        if actor is None or not actor.active or actor.role != "admin":
            raise ImportProblem("활성 로컬 테스트 관리자가 필요합니다.")
        return save_or_activate(actor, body=ConnectionInput(host="127.0.0.1", port=runtime["port"],
            database=database, username="wafer_source", password=runtime["source_password"], sslmode="prefer"), activate=activate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / ".local/imports/export_01")
    parser.add_argument("--database", required=True)
    parser.add_argument("--activate", action="store_true", help="등록 후 앱의 활성 DB를 전환합니다.")
    parser.add_argument("--register-only", action="store_true", help="이미 반입한 DB를 재검증해 등록합니다. 파일은 다시 적재하지 않습니다.")
    args = parser.parse_args()
    try:
        if args.register_only:
            runtime = managed_runtime()
            with connect(runtime, args.database, reader=True) as connection:
                # Marker is private to the import owner; check through the owner below.
                connection.execute("SELECT 1")
            with connect(runtime, args.database) as connection:
                report = connection.execute("SELECT report FROM import_data.snapshot_info").fetchone()[0]
            if report["database"] != args.database:
                raise ImportProblem("반입 이력을 확인할 수 없습니다.")
        else:
            report = import_snapshot(args.input, args.database)
        registration = register_snapshot(args.database, activate=args.activate)
        report.update(registered=True, active=registration["active"], profile_id=registration["profile_id"])
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except ImportProblem as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        # COPY/Arrow exception text can contain data values or file credentials.
        print(f"반입 또는 등록 실패 ({type(exc).__name__}). 기존 DB는 덮어쓰지 않았습니다. 파일 구성·타입·로컬 DB 설정을 확인하세요. 적재 후 등록만 실패했다면 --register-only로 다시 등록하세요.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
