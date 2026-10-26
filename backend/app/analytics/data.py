"""Explicit adapter for the user's Inline/EPM/wafer-history measurement schema.

Reads registered, visible sources only. Limits fail closed; never silently sample.
"""
from collections import defaultdict
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .. import db as database
from ..expressions import quote
from ..models import Source, utcnow
from ..source_policy import require_read_only_role
from ..table_visibility import source_visible
from .cancellation import checkpoint

MAX_ROWS = 1_000_000
MAX_SERIES = 5000
TABLES = {"inline": "inline_data", "epm": "epm_data", "history": "wafer_his"}
KEYS = ["lot_cd", "lot_id", "wf_id"]
MEASUREMENT = KEYS + ["die_x", "die_y", "parameter", "value", "end_dt_tm", "record_id"]
REQUIRED = {"inline": MEASUREMENT + ["operation"], "epm": MEASUREMENT,
            "history": KEYS + ["operation", "recipe", "end_dt_tm", "record_id"]}


def source_snapshot(db):
    result, issues = {}, []
    sources = list(db.scalars(select(Source).where(Source.connection_key == database.source_database_key())))
    for family, table in TABLES.items():
        matches = [s for s in sources if s.table_name == table and source_visible(s)]
        if len(matches) != 1:
            issues.append(f"{table}: 사용 가능한 원천 테이블을 하나로 확인해야 합니다.")
            continue
        source = matches[0]
        names = {c["name"] for c in source.columns}
        missing = set(REQUIRED[family]) - names
        if missing:
            issues.append(f"{table}: 필요한 컬럼이 없습니다 ({', '.join(sorted(missing))}).")
            continue
        result[family] = {"source_id": source.id, "name": source.name,
                          "schema_name": source.schema_name, "table_name": source.table_name,
                          "columns": source.columns}
    return result, issues


def validate_snapshot(db, snapshot, *, sources=None, allow_column_additions=False):
    from ..source_compatibility import columns_preserved
    for source in snapshot.values():
        current = sources.get(source["source_id"]) if sources is not None else db.get(Source, source["source_id"])
        if (not current or not source_visible(current) or
                not (columns_preserved(source['columns'], current.columns) if allow_column_additions else current.columns == source['columns'])
                or current.schema_name != source["schema_name"] or current.table_name != source["table_name"]):
            raise HTTPException(409, "분석 원천의 공개 범위 또는 스키마가 변경되었습니다. 새 분석을 요청하세요.")


def qualified(source):
    return quote(source["schema_name"]) + "." + quote(source["table_name"])


@contextmanager
def analysis_connection():
    binding = database.current_source()
    if not binding.engine:
        raise HTTPException(503, "분석할 DB 연결을 확인하세요.")
    try:
        with binding.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, public")
                connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                                   {"timeout": str(database.settings.query_timeout_ms)})
                require_read_only_role(connection)
                yield connection
    except SQLAlchemyError:
        raise HTTPException(503, "분석 원천 조회에 실패했습니다. 연결·스키마·조회 제한 시간을 확인하세요.") from None


def catalog(db):
    snapshot, issues = source_snapshot(db)
    result = {"sources": [{"family": family, **{k: s[k] for k in ("source_id", "name")}, "available": True}
                          for family, s in snapshot.items() if family != "history"],
              "series": [], "recipe_operations": [], "extent": None,
              "limits": {"max_rows": MAX_ROWS, "max_series": MAX_SERIES},
              "supported": bool(snapshot.get("inline") or snapshot.get("epm")), "issues": issues}
    if not snapshot:
        return result
    extents = []
    with analysis_connection() as connection:
        for family in ("inline", "epm"):
            if family not in snapshot:
                continue
            table = qualified(snapshot[family])
            operation = '"operation"' if family == "inline" else "''"
            rows = connection.execute(text(f"SELECT DISTINCT {operation} AS operation, parameter FROM {table} "
                                           "ORDER BY operation, parameter LIMIT :limit"), {"limit": MAX_SERIES + 1}).mappings().all()
            if len(result["series"]) + len(rows) > MAX_SERIES:
                raise HTTPException(422, "현재 분석기의 PARA 수 한도를 초과했습니다. 분석 범위를 분할해야 합니다.")
            result["series"].extend({"family": family, **dict(row)} for row in rows)
            extent = connection.execute(text(f"SELECT min(end_dt_tm), max(end_dt_tm) FROM {table}")).one()
            extents.extend(value for value in extent if value is not None)
        if "history" in snapshot:
            rows = connection.execute(text(f"SELECT DISTINCT operation, recipe FROM {qualified(snapshot['history'])} "
                                           "ORDER BY operation, recipe LIMIT 10001")).all()
            if len(rows) > 10000:
                result["issues"].append("Recipe 목록이 10,000개를 초과했습니다. 목록 범위를 분할해야 합니다.")
            else:
                operations = defaultdict(list)
                for operation, recipe in rows:
                    operations[operation].append(recipe)
                result["recipe_operations"] = [{"operation": op, "recipes": recipes} for op, recipes in operations.items()]
    if extents:
        result["extent"] = {"start": min(extents).isoformat(), "end": max(extents).isoformat()}
    return result


def selected_snapshot(snapshot, request):
    families = [request.family] if request.family else ["inline", "epm"]
    if request.kind == "split":
        families.append("history")
    if any(f not in snapshot for f in families):
        raise HTTPException(422, "선택한 분석에 필요한 원천 테이블을 확인하세요.")
    return {f: snapshot[f] for f in families}


def extract(snapshot, request, *, project_lot_cds=None):
    measurements, histories, watermark = [], [], {}
    total = 0
    with analysis_connection() as connection:
        captured_at = connection.scalar(text("SELECT transaction_timestamp()"))
        for family, source in snapshot.items():
            checkpoint()
            predicates, params = [], {}
            if project_lot_cds is not None:
                predicates.append("lot_cd = ANY(:project_lot_cds)")
                params["project_lot_cds"] = project_lot_cds
            if family == "history":
                predicates += ["operation = :operation", "end_dt_tm < :end"]
                params.update(operation=request.operation, end=request.end)
                # Keep all earlier recipe events, including other recipes, for a correct as-of match.
            else:
                if request.kind == "trend":
                    predicates.append("((end_dt_tm >= :bs AND end_dt_tm < :be) OR (end_dt_tm >= :cs AND end_dt_tm < :ce))")
                    params.update(bs=request.baseline_start, be=request.baseline_end, cs=request.current_start, ce=request.current_end)
                elif request.start and request.end:
                    predicates.append("end_dt_tm >= :start AND end_dt_tm < :end")
                    params.update(start=request.start, end=request.end)
                if request.parameter:
                    predicates.append("parameter = :parameter")
                    params["parameter"] = request.parameter
                if family == "inline" and request.operation and request.kind != "split":
                    predicates.append("operation = :operation")
                    params["operation"] = request.operation
            columns = ", ".join(quote(c) for c in REQUIRED[family])
            where = " WHERE " + " AND ".join(predicates) if predicates else ""
            params["limit"] = MAX_ROWS - total + 1
            sql = f"SELECT {columns} FROM {qualified(source)}{where} LIMIT :limit"
            count, last_at, last_record = 0, None, None
            with connection.execution_options(yield_per=10000).execute(text(sql), params) as cursor:
                # Column names are constant for this cursor. Mapping each row
                # asks SQLAlchemy to rebuild its key view hundreds of thousands
                # of times; zip the fixed names with the raw row instead.
                column_names = tuple(cursor.keys())
                for row in cursor:
                    if total % 2048 == 0:
                        checkpoint()
                    total += 1
                    if total > MAX_ROWS:
                        raise HTTPException(422, f"분석 대상이 {MAX_ROWS:,}행을 초과했습니다. 기간 또는 PARA 범위를 줄여 주세요. 일부 행만으로 계산하지 않았습니다.")
                    item = dict(zip(column_names, row))
                    item["measured_at"] = item.pop("end_dt_tm")
                    if item["measured_at"] is None or item["measured_at"].tzinfo is None:
                        raise HTTPException(422, "측정 시각의 시간대 또는 NULL 처리 정의가 필요합니다.")
                    count += 1
                    if last_at is None or item["measured_at"] > last_at:
                        last_at = item["measured_at"]
                    if last_record is None or item["record_id"] > last_record:
                        last_record = item["record_id"]
                    if family == "history":
                        histories.append(item)
                    else:
                        item.update(family=family, operation=item.get("operation", ""))
                        measurements.append(item)
            watermark[family] = {"source_id": source["source_id"], "row_count": count,
                                 "last_measured_at": last_at.isoformat() if last_at else None,
                                 "max_record_id": last_record}
    return measurements, histories, {"captured_at": captured_at.isoformat(), "sources": watermark,
                                     "isolation": "repeatable_read", "scope": "requested_rows"}
