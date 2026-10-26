import base64
import hashlib
import json
import math
import time
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from starlette.responses import Response
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError

from . import db as database
from .db import SessionLocal, settings
from .expressions import compile_expression, quote, quote_output
from .card_expressions import compile_card_expression
from .editor_filters import editable_filters
from .models import DatasetVersion, Relation, Source
from .raw_sql import compile_form, compile_raw_sql, form_to_sql
from .direct_sql import embedded_query, parse_query, referenced_sources, sqlalchemy_literal
from .schemas import CardSpec, DatasetSpec, Filter, QueryContext
from .source_policy import require_read_only_role
from .table_visibility import TableVisibility, require_source_visible, source_visible
from .dashboard_period import apply_period
from .chart_cache import ChartCache, ChartPayload

# A dashboard polls every minute. Longer configured TTLs must not keep serving
# an old result indefinitely to those polls; concurrent viewers share this cache.
_chart_cache = ChartCache(lambda: min(settings.cache_ttl_seconds, 60))
MAX_AGGREGATES = 2000


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value


@contextmanager
def source_connection():
    binding = database.current_source()
    if binding.engine is None:
        raise HTTPException(503, binding.error or "DB 설정에서 PostgreSQL을 연결해 주세요.")
    try:
        with binding.engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, public")
                connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                                   {"timeout": str(settings.query_timeout_ms)})
                require_read_only_role(connection)
                yield connection
    except DBAPIError as error:
        code = getattr(error.orig, "sqlstate", "")
        if code == "57014":
            raise HTTPException(408, "조회 제한 시간을 초과했습니다. 필터 범위를 줄여 주세요.") from None
        if code in {"42703", "42P01", "42804", "42883"}:
            raise HTTPException(409, "원본 스키마 또는 표현식 타입이 변경되었습니다. 데이터셋을 확인하세요.") from None
        if code in {"42803", "42P20", "42601"}:
            message = getattr(getattr(error.orig, "diag", None), "message_primary", None)
            raise HTTPException(422, message or "집계식과 SQL 문법을 확인하세요.") from None
        if code in {"22012", "22P02", "22003", "22007", "22008", "22023", "42846"}:
            raise HTTPException(422, "계산식의 나눗셈·형변환·값 범위를 확인하세요.") from None
        if code == "42501":
            raise HTTPException(403, "원본 DB 조회 권한이 없습니다.") from None
        if code == "25006":
            raise HTTPException(403, "원천 DB는 읽기 전용입니다. 데이터나 스키마를 변경할 수 없습니다.") from None
        raise HTTPException(503, "PostgreSQL 조회에 실패했습니다. 연결 상태와 서버 로그를 확인하세요.") from None


def execute_rows(sql, params=None):
    with source_connection() as connection:
        return [clean(dict(row)) for row in connection.execute(text(sql), params or {}).mappings()]


def stream_rows(sql, params=None):
    """A read-only server cursor keeps large Python inputs out of API memory."""
    from .plotly_policy import BATCH_ROWS
    with source_connection() as connection:
        with connection.execution_options(yield_per=BATCH_ROWS).execute(text(sql), params or {}) as result:
            for partition in result.partitions(BATCH_ROWS):
                for row in partition:
                    yield tuple(clean(value) for value in row)


def source_json(source):
    return {k: getattr(source, k) for k in ("id", "name", "schema_name", "table_name", "project_column", "primary_key", "columns")}


def dataset_json(dataset, *, catalog=None):
    result = {k: getattr(dataset, k) for k in ("id", "name", "version", "project_id", "columns", "spec")}
    if catalog is not None:
        from .dataset_scope import dataset_kind
        result['kind'] = dataset_kind(dataset, catalog)
        spec = dataset.spec
        if spec.get("raw_sql"):
            try:
                sources = referenced_sources(parse_query(spec["raw_sql"]), catalog.catalog)
                result["source_tables"] = [f"{s['schema_name']}.{s['table_name']}" for s in sources]
            except HTTPException:
                # Broken saved SQL must remain editable; do not present a partial
                # list from source_id as if it described the authored query.
                result["source_tables"] = None
        else:
            ids = [spec["source_id"], *(catalog.relations[r].right_source_id
                   for r in spec.get("joins", []) if r in catalog.relations)]
            result["source_tables"] = list(dict.fromkeys(
                f"{catalog.sources[id].schema_name}.{catalog.sources[id].table_name}"
                for id in ids if id in catalog.sources))
    return result


def assistant_catalog(dataset_id=None, project_id=None):
    from .project_settings import managed_project
    project = managed_project(project_id)
    with SessionLocal() as db:
        latest = select(DatasetVersion.id, func.max(DatasetVersion.version).label("version")).group_by(DatasetVersion.id).subquery()
        query = select(DatasetVersion).join(latest, (DatasetVersion.id == latest.c.id) & (DatasetVersion.version == latest.c.version))
        query = query.where(DatasetVersion.project_id == project_id)
        if dataset_id:
            query = query.where(DatasetVersion.id == dataset_id)
        if project:
            query = query.where(~(DatasetVersion.id.like('auto_%') & DatasetVersion.project_id.is_(None)))
        visibility = TableVisibility.load(db)
        return [dataset_json(item, catalog=visibility) for item in db.scalars(query.order_by(DatasetVersion.name)) if visibility.allows_spec(item.spec)]


def load_dataset(dataset_id, version=None, *, with_catalog=False):
    with SessionLocal() as db:
        dataset = (db.scalar(select(DatasetVersion).where(DatasetVersion.id == dataset_id)
                             .order_by(DatasetVersion.version.desc()).limit(1)) if version is None
                   else db.get(DatasetVersion, (dataset_id, version)))
        if not dataset:
            raise HTTPException(404, "데이터셋을 찾을 수 없습니다." if version is None else "데이터셋 버전을 찾을 수 없습니다.")
        catalog = TableVisibility.load(db, include_relations=bool(dataset.spec.get("joins")))
        if not catalog.allows_spec(dataset.spec):
            raise HTTPException(404, "앱에서 제외된 테이블을 사용하는 데이터셋입니다.")
        return (dataset, catalog) if with_catalog else dataset


def scoped_context(project_id, context):
    """An owned dataset cannot broaden its execution to another project."""
    from .project_settings import managed_project
    managed_project(project_id if project_id is not None else context.project_id)
    if project_id is None:
        return context
    if context.project_id is not None and context.project_id != project_id:
        raise HTTPException(422, "이 데이터셋은 다른 프로젝트 전용입니다. 해당 프로젝트에서 조회하세요.")
    return context.model_copy(update={"project_id": project_id})


def _filter_sql(filters, names, params, prefix, ignored=None):
    conditions = []
    for index, item in enumerate(filters):
        if not isinstance(item, Filter):
            item = Filter.model_validate(item)
        if item.column not in names:
            if ignored is not None:
                ignored.append(item.column)
                continue
            raise HTTPException(422, f"필터 컬럼이 없습니다: {item.column}")
        col = quote_output(item.column)
        key = f"{prefix}_{index}"
        if item.value is None and item.op in {"eq", "ne"}:
            conditions.append(f"{col} IS {'NOT ' if item.op == 'ne' else ''}NULL")
        elif item.op in {"in", "not_in"}:
            operator = "NOT IN" if item.op == "not_in" else "IN"
            if not isinstance(item.value, list) or len(item.value) > 100:
                raise HTTPException(422, f"{operator} 필터는 100개 이하의 값 목록이어야 합니다.")
            keys = []
            for j, val in enumerate(item.value):
                if isinstance(val, (list, dict)):
                    raise HTTPException(422, "필터 값은 단일 값이어야 합니다.")
                params[f"{key}_{j}"] = val
                keys.append(f":{key}_{j}")
            conditions.append(f"{col} {operator} ({', '.join(keys)})" if keys else "TRUE" if item.op == "not_in" else "FALSE")
        elif item.op == "contains":
            if not isinstance(item.value, str) or len(item.value) > 2000:
                raise HTTPException(422, "contains 필터는 문자열이어야 합니다.")
            params[key] = "%" + item.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append(f"CAST({col} AS text) ILIKE :{key} ESCAPE E'\\\\'")
        else:
            if isinstance(item.value, (dict, list)):
                raise HTTPException(422, "필터 값은 단일 값이어야 합니다.")
            operator = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}.get(item.op)
            if not operator:
                raise HTTPException(422, "지원하지 않는 필터입니다.")
            params[key] = item.value
            conditions.append(f"{col} {operator} :{key}")
    return " AND ".join(conditions) or "TRUE"


def validate_dataset(spec: DatasetSpec | dict, *, catalog=None):
    if not isinstance(spec, DatasetSpec):
        spec = DatasetSpec.model_validate(spec)
    with (SessionLocal() if catalog is None else nullcontext()) as db:
        source = db.get(Source, spec.source_id) if catalog is None else catalog.sources.get(spec.source_id)
        if not source:
            raise HTTPException(404, "등록된 원본을 찾을 수 없습니다.")
        require_source_visible(source)
        source = source_json(source)
        if spec.pivot is not None:
            from .pivot import pivot_definition
            spec = pivot_definition(spec, source)
        relations = []
        if len(spec.joins) != len(set(spec.joins)):
            raise HTTPException(422, "중복 관계를 선택할 수 없습니다.")
        if spec.raw_sql is not None:
            tree = parse_query(spec.raw_sql)
            available = db.scalars(select(Source)) if catalog is None else catalog.sources.values()
            sources = referenced_sources(tree, [source_json(item) for item in available if source_visible(item)])
            return {"direct_sql": spec.raw_sql, "spec": spec, "source": source, "sources": sources,
                    "relations": [], "columns": [], "base_columns": source["columns"]}
        for relation_id in spec.joins if not relations else []:
            relation = db.get(Relation, relation_id) if catalog is None else catalog.relations.get(relation_id)
            if not relation or relation.left_source_id != source["id"]:
                raise HTTPException(422, "선택한 관계가 이 원본에 등록되어 있지 않습니다.")
            target = db.get(Source, relation.right_source_id) if catalog is None else catalog.sources.get(relation.right_source_id)
            require_source_visible(target)
            info = {k: getattr(relation, k) for k in ("id", "left_column", "right_column", "join_type")}
            info["join_type"] = spec.join_types.get(relation_id,info["join_type"])
            relations.append((info, source_json(target)))
    columns = list(source["columns"])
    for relation, target in relations:
        for column in target["columns"]:
            columns.append({"name": f"{target['id']}__{column['name']}", "type": column["type"]})
    if len({col["name"] for col in columns}) != len(columns):
        raise HTTPException(422, "관계 출력 컬럼 이름이 충돌합니다.")
    names = {col["name"] for col in columns}
    compiled = compile_form(spec, source, relations)
    _filter_sql(spec.filters,{c["name"] for c in compiled["columns"]},{},"validate")
    return dict(compiled,spec=spec,source=source,relations=relations,base_columns=columns)


def normalize_definition(spec, project_id=None):
    compiled = validate_dataset(spec)
    if "direct_sql" in compiled:
        return compiled["spec"], compiled["direct_sql"]
    sql = form_to_sql(compiled["spec"],compiled["source"],project_id,compiled["relations"])
    canonical = compile_raw_sql(sql,compiled["source"],compiled["relations"])["spec"]
    return editable_filters(canonical), sql


def _dataset_cte(compiled, context, extra_filters=(), *, include_row_id=True):
    from .dataset_scope import scoped_sql, project_sources
    if "direct_sql" in compiled:
        params, ignored = {}, []
        names = {column["name"] for column in compiled["columns"]}
        conditions = [_filter_sql(context.filters, names, params, "q", ignored),
                      _filter_sql(extra_filters, names, params, "c")]
        original = sqlalchemy_literal(embedded_query(scoped_sql(compiled, context.project_id)))
        identity = ', ROW_NUMBER() OVER () AS "__row_id"' if include_row_id else ''
        cte = f'WITH "__pm_query" AS ({original}), filtered AS (SELECT "__pm_query".*{identity} FROM "__pm_query" WHERE {" AND ".join(conditions)})'
        return cte, params, sorted(set(ignored))
    source, spec = compiled["source"], compiled["spec"]
    project_predicates = {s['id']: p for s, p in project_sources(compiled, context.project_id)}
    params = dict(compiled["params"])
    source_table = quote(source["schema_name"]) + "." + quote(source["table_name"])
    row_id = f's.{quote(source["primary_key"])}' if source["primary_key"] else "ROW_NUMBER() OVER ()"
    selects = [f'{row_id} AS "__row_id"'] if include_row_id else []
    selects += [f's.{quote(col["name"])} AS {quote(col["name"])}' for col in source["columns"]]
    if context.project_id is not None:
        params["project_id"] = context.project_id
    def source_scope(target):
        predicates = []
        if target['id'] in project_predicates:
            predicate = project_predicates[target['id']]
            if predicate.key == 'eq' and target.get('project_column'):
                predicates.append(f'{quote(target["project_column"])} = :project_id')
            else:
                predicates.append(sqlalchemy_literal(predicate.sql(dialect='postgres')))
        period = compiled.get("source_period")
        if period and all(target[name] == period[name] for name in ("schema_name", "table_name")):
            params["dashboard_period_start"] = period["value"]
            predicates.append(f'{quote(period["column"])} >= CAST(:dashboard_period_start AS {period["cast"]})')
        return " WHERE " + " AND ".join(predicates) if predicates else ""
    # Scope the base relation before any joins; project dimensions are scoped separately.
    scope = source_scope(source)
    from_sql = f"(SELECT * FROM {source_table}{scope}) s"
    for index, (relation, target) in enumerate(compiled["relations"]):
        alias = f"j{index}"
        table = quote(target["schema_name"]) + "." + quote(target["table_name"])
        scope2 = source_scope(target)
        conditions = [f"s.{quote(relation['left_column'])} = {alias}.{quote(relation['right_column'])}"]
        if source["project_column"] and target["project_column"]:
            conditions.append(f"s.{quote(source['project_column'])} = {alias}.{quote(target['project_column'])}")
        from_sql += f" {relation['join_type'].upper()} JOIN (SELECT * FROM {table}{scope2}) {alias} ON {' AND '.join(conditions)}"
        selects += [f"{alias}.{quote(c['name'])} AS {quote(target['id'] + '__' + c['name'])}" for c in target["columns"]]
    grouped = compiled.get("grouped",False)
    identity = 'MIN(CAST("__row_id" AS text)) AS "__row_id"' if grouped else '"__row_id"'
    outputs = ([identity] if include_row_id else []) + [f"{compiled['expressions'].get(col['name'], quote_output(col['name']))} AS {quote_output(col['name'])}" for col in compiled["columns"]]
    names = {col["name"] for col in compiled["columns"]}
    ignored = []
    base_names = {c["name"] for c in compiled["base_columns"]}
    input_filters = [f for f in context.filters if grouped and f.column in base_names]
    output_filters = [f for f in context.filters if f not in input_filters]
    predicates = [_filter_sql(spec.filters, names, params, "d"),
                  _filter_sql(output_filters, names, params, "q", ignored),
                  _filter_sql(extra_filters, names, params, "c")]
    # A user's raw WHERE only sees the already project-scoped common relation.
    # It cannot remove or replace the server-owned project predicate above.
    raw_where = compiled.get("where_sql", "TRUE")
    input_where = _filter_sql(input_filters,base_names,params,"input")
    group_sql = " GROUP BY " + ", ".join(compiled["group_sql"]) if compiled.get("group_sql") else ""
    having_sql = " HAVING " + compiled["having_sql"] if grouped else ""
    cte = f"WITH base AS (SELECT {', '.join(selects)} FROM {from_sql}), derived AS (SELECT {', '.join(outputs)} FROM base WHERE ({raw_where}) AND ({input_where}){group_sql}{having_sql}), filtered AS (SELECT * FROM derived WHERE {' AND '.join(predicates)})"
    return cte, params, sorted(set(ignored))


def _type_columns(rows, columns):
    # pg_typeof reports the actual SQL projection types even when expressions change types.
    if not rows:
        return columns
    return [{"name": col["name"], "type": rows[0].get(f"t{index}", col["type"])} for index, col in enumerate(columns)]


def _schema_types(compiled):
    cte, params, _ = _dataset_cte(compiled, QueryContext())
    # LIMIT 0 resolves the query types without reading or materializing source rows.
    with source_connection() as connection:
        result = connection.execute(text(f"{cte} SELECT * FROM filtered LIMIT 0"), params)
        descriptors = [(column.name, column.type_code) for column in result.cursor.description if column.name != "__row_id"]
        oids = sorted({oid for name, oid in descriptors})
        binds = {f"oid{i}": oid for i, oid in enumerate(oids)}
        types = dict(connection.execute(text("SELECT oid,format_type(oid,NULL) FROM pg_catalog.pg_type WHERE oid IN (" + ",".join(f":oid{i}" for i in range(len(oids))) + ")"),binds).all())
        return [{"name":name,"type":types[oid]} for name,oid in descriptors]


def _encode_cursor(row_id, value, key):
    raw = json.dumps({"id": row_id, "value": value, "key": key}, separators=(",", ":"), default=str)
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor, key):
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor).decode())
        if not isinstance(value, dict) or value.get("key") != key or not isinstance(value.get("id"), (str, int, float)) or isinstance(value.get("value"), (list, dict)):
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(422, "커서가 올바르지 않거나 현재 조회 조건과 다릅니다.") from None


def query_compiled(compiled, context, key="preview", include_ids=False):
    if "direct_sql" in compiled:
        return query_direct(compiled, context, key)
    cte, params, ignored = _dataset_cte(compiled, context)
    names = {col["name"] for col in compiled["columns"]}
    sort = context.sort or "__row_id"
    if sort != "__row_id" and sort not in names:
        raise HTTPException(422, "정렬 컬럼이 없습니다.")
    key = hashlib.sha256(json.dumps([key, context.model_dump(exclude={"cursor", "refresh", "limit"}), compiled["spec"].model_dump()], sort_keys=True).encode()).hexdigest()[:20]
    order, operator = ("ASC", ">") if context.direction == "asc" else ("DESC", "<")
    seek = "TRUE"
    if context.cursor:
        cursor = _decode_cursor(context.cursor, key)
        params.update(cursor_id=cursor["id"], cursor_value=cursor.get("value"))
        if sort == "__row_id":
            seek = f'"__row_id" {operator} :cursor_id'
        elif cursor.get("value") is None:
            seek = f'{quote_output(sort)} IS NULL AND "__row_id" {operator} :cursor_id'
        else:
            seek = f'({quote_output(sort)} {operator} :cursor_value OR {quote_output(sort)} IS NULL OR ({quote_output(sort)} = :cursor_value AND "__row_id" {operator} :cursor_id))'
    limit = min(context.limit, 1000)
    params["page_limit"] = limit + 1
    rows = execute_rows(f'{cte} SELECT * FROM filtered WHERE {seek} ORDER BY {quote_output(sort)} {order} NULLS LAST, "__row_id" {order} LIMIT :page_limit', params)
    has_more = len(rows) > limit
    rows = rows[:limit]
    cursor = _encode_cursor(rows[-1]["__row_id"], rows[-1].get(sort), key) if has_more else None
    row_ids = [row.pop("__row_id", None) for row in rows]
    result = {"columns": compiled["columns"], "rows": rows, "next_cursor": cursor,
            "meta": {"mode": "aggregate" if compiled.get("grouped") else "full", "returned_rows": len(rows), "queried_at": datetime.now(timezone.utc).isoformat(), "cached": False, "ignored_filters": ignored, "page_size": limit, "has_more": has_more}}
    if include_ids:
        result["_row_ids"] = row_ids
    return result


def query_direct(compiled, context, key="preview"):
    """Preserve authored SQL; enforce project scope at every physical source."""
    from .dataset_scope import scoped_sql
    execution_sql = scoped_sql(compiled, context.project_id)
    limit = min(context.limit, 1000)
    key = hashlib.sha256(json.dumps([key, execution_sql, context.model_dump(exclude={"cursor", "refresh", "limit"})], sort_keys=True).encode()).hexdigest()[:20]
    offset = _decode_cursor(context.cursor, key)["id"] if context.cursor else 0
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1000000:
        raise HTTPException(422, "조회 페이지가 올바르지 않습니다.")
    original, params, ignored = execution_sql, {}, []
    sql = original
    wrapped = bool(context.filters or context.sort)
    if wrapped:
        cte, params, ignored = _dataset_cte(compiled, context)
        names = {column["name"] for column in compiled["columns"]}
        if context.sort and context.sort not in names:
            raise HTTPException(422, "정렬 컬럼이 없습니다.")
        sql = cte + " SELECT * FROM filtered"
        if context.sort:
            sql += f' ORDER BY {quote_output(context.sort)} {context.direction.upper()} NULLS LAST, "__row_id"'
    with source_connection() as connection:
        # Driver SQL is literal; SQLAlchemy text() wrappers still need their
        # parameter/percent handling even when the parameter mapping is empty.
        streaming = connection.execution_options(stream_results=True, no_parameters=not wrapped)
        result = streaming.execute(text(sql), params) if wrapped else streaming.exec_driver_sql(sql)
        descriptors = [(column.name, column.type_code) for column in result.cursor.description if not wrapped or column.name != "__row_id"]
        names = [name for name, _ in descriptors]
        if not names or len(names) > 100 or len(set(names)) != len(names) or any(name.startswith("__") for name in names):
            result.close()
            raise HTTPException(422, "결과 컬럼은 중복 없이 1~100개여야 합니다. 중복 이름에는 AS 별칭을 지정하고 __로 시작하는 이름은 사용하지 마세요.")
        remaining = offset
        while remaining:
            skipped = result.fetchmany(min(remaining, 1000))
            remaining -= len(skipped)
            if not skipped:
                break
        rows = [clean(dict(row)) for row in result.mappings().fetchmany(limit + 1)]
        result.close()
        oids = sorted({oid for _, oid in descriptors})
        binds = {f"oid{i}": oid for i, oid in enumerate(oids)}
        types = dict(connection.execute(text("SELECT oid,format_type(oid,NULL) FROM pg_catalog.pg_type WHERE oid IN (" + ",".join(f":oid{i}" for i in range(len(oids))) + ")"), binds).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    if wrapped:
        for row in rows:
            row.pop("__row_id", None)
    return {"columns": [{"name": name, "type": types[oid]} for name, oid in descriptors], "rows": rows,
            "next_cursor": _encode_cursor(offset + limit, None, key) if has_more else None,
            "meta": {"mode": "sql", "returned_rows": len(rows), "queried_at": datetime.now(timezone.utc).isoformat(),
                     "cached": False, "ignored_filters": ignored, "page_size": limit, "has_more": has_more}}


def table_query(dataset_id, version, context):
    dataset = load_dataset(dataset_id, version)
    context = scoped_context(dataset.project_id, context)
    # Legacy form definitions use the same SQL exported to the editor. New
    # definitions already store raw_sql, which normalize_definition preserves.
    spec, sql = normalize_definition(dataset.spec, context.project_id)
    compiled = validate_dataset(DatasetSpec(source_id=spec.source_id, raw_sql=sql))
    compiled["columns"] = dataset.columns
    return query_compiled(compiled, context, f"{dataset_id}:{version}")


def preview_dataset(spec, context=None):
    context = context or QueryContext()
    context = context.model_copy(update={"limit": min(context.limit, 100), "cursor": None})
    if not isinstance(spec, DatasetSpec):
        spec = DatasetSpec.model_validate(spec)
    if spec.pivot is not None:
        spec = validate_dataset(spec)['spec']
    if spec.raw_sql is not None:
        compiled = validate_dataset(spec)
        result = query_direct(compiled, context.model_copy(update={"filters": [], "sort": None}))
        result.update(before_rows=[], sql=spec.raw_sql, canonical_sql=spec.raw_sql, spec=spec.model_dump())
        result["meta"]["before_mode"] = "unavailable"
        return result
    canonical, canonical_sql = normalize_definition(spec,context.project_id)
    compiled = validate_dataset(canonical)
    compiled["columns"] = _schema_types(compiled)
    result = query_compiled(compiled, context, include_ids=True)
    row_ids = result.pop("_row_ids")
    before = validate_dataset(DatasetSpec(source_id=compiled["spec"].source_id, joins=compiled["spec"].joins,join_types=compiled["spec"].join_types))
    before_context = context.model_copy(update={"sort": None, "filters": []})
    before_cte, before_params, _ = _dataset_cte(before, before_context)
    before_params.update({f"row{i}":value for i,value in enumerate(row_ids)})
    if compiled.get("grouped"):
        before["where_sql"],before["params"] = compiled["where_sql"],dict(compiled["params"])
        before_context = context.model_copy(update={"sort":None,"filters":[f for f in context.filters if f.column in {c["name"] for c in before["base_columns"]}]})
        result["before_rows"] = query_compiled(before,before_context)["rows"]
        result["meta"]["before_mode"] = "source_sample"
    elif row_ids:
        placeholders = ",".join(f":row{i}" for i in range(len(row_ids)))
        before_rows = execute_rows(f'{before_cte} SELECT * FROM filtered WHERE "__row_id" IN ({placeholders})',before_params)
        by_id = {row.pop("__row_id"):row for row in before_rows}
        result["before_rows"] = [by_id[row_id] for row_id in row_ids]
    else:
        result["before_rows"] = []
    cte, params, _ = _dataset_cte(compiled, context)
    result["sql"] = cte + " SELECT * FROM filtered LIMIT 100"
    result["canonical_sql"] = canonical_sql
    result["spec"] = canonical.model_dump()
    result["meta"]["parameters"] = clean(params)
    return result


def validate_card(card, dataset=None, *, catalog=None):
    # Cards follow the latest definition. Explicit versions remain available to
    # the dataset editor/table history, while draft proposals supply a dataset.
    dataset = dataset if dataset is not None else load_dataset(card.dataset_id, None)
    compiled = validate_dataset(dataset.spec) if catalog is None else validate_dataset(dataset.spec, catalog=catalog)
    compiled["columns"] = dataset.columns
    compiled["project_id"] = dataset.project_id
    compiled["dataset_version"] = getattr(dataset, "version", card.dataset_version)
    if card.editor_mode == 'python':
        from .plotly_scripts import validate_script
        validate_script(card.python_script)
        _filter_sql(card.filters, {c['name'] for c in dataset.columns}, {}, 'card')
        if card.script_input == 'dataset':
            return compiled
    if card.kind == 'gantt':
        from .gantt import validate_gantt
        return validate_gantt(card, compiled, catalog)
    names = {c["name"] for c in dataset.columns}
    if card.value_expression is not None:
        if card.kind in {"wafer"} and card.color:
            raise HTTPException(422, "셀 값 컬럼과 직접 계산식 중 하나만 선택하세요.")
        compiled["card_expression"] = compile_card_expression(card, dataset.columns)
    if card.trellis and card.kind not in {"bar", "line", "scatter", "histogram", "box", "wafer"}:
        raise HTTPException(422, "이 차트에는 Trellis를 사용할 수 없습니다.")
    for name in (card.x, card.y, card.color, card.trellis):
        if name and name not in names:
            raise HTTPException(422, f"최신 데이터셋에 컬럼이 없습니다. 카드 설정을 수정하세요: {name}")
    _filter_sql(card.filters, names, {}, "card")
    if card.kind != "kpi" and not card.x:
        raise HTTPException(422, "X 컬럼을 선택하세요.")
    if (card.kind in {"wafer"} or (card.kind in {"scatter", "box"} and card.value_expression is None)) and not card.y:
        raise HTTPException(422, "Y 컬럼을 선택하세요.")
    if card.aggregate == "yield" and not {"tested_dies", "passed_dies"}.issubset(names):
        raise HTTPException(422, "수율 집계에는 tested_dies와 passed_dies 컬럼이 필요합니다.")
    if card.aggregate in {"sum", "avg", "min", "max"} and not card.y and card.value_expression is None:
        raise HTTPException(422, "집계할 Y 컬럼을 선택하세요.")
    if card.kind in {"wafer"} and card.aggregate in {"sum", "avg", "min", "max"} and not card.color and card.value_expression is None:
        raise HTTPException(422,"셀에 표시할 값 컬럼을 선택하세요.")
    if card.kind in {"bar", "line"} and card.aggregate == "none" and not card.y and card.value_expression is None:
        raise HTTPException(422, "집계하지 않을 경우 Y 컬럼을 선택하세요.")
    numeric = {"smallint", "integer", "bigint", "numeric", "real", "double precision", "decimal"}
    types = {c["name"]:c["type"] for c in dataset.columns}
    if card.trellis and (types[card.trellis].lower() in {"json", "jsonb", "bytea", "record", "array"} or types[card.trellis].endswith("[]")):
        raise HTTPException(422, "Trellis 구분에는 문자·숫자·날짜 같은 단일 값 컬럼을 선택하세요.")
    numeric_columns = []
    if card.aggregate == "yield":
        numeric_columns.extend(["tested_dies", "passed_dies"])
    if card.kind == "histogram":
        numeric_columns.append(card.x)
    if card.value_expression is None and ((card.aggregate in {"sum", "avg"} and card.kind not in {"wafer"}) or card.kind in {"scatter", "box"}):
        numeric_columns.append(card.y)
    if card.kind in {"wafer"} and card.color:
        numeric_columns.append(card.color)
    for name in numeric_columns:
        if name and types[name] not in numeric:
            raise HTTPException(422,f"숫자형 컬럼을 선택하세요: {name}")
    from .card_scales import validate_axis_scales
    validate_axis_scales(card, dataset.columns)
    for minimum,maximum in ((card.options.x_min,card.options.x_max),(card.options.y_min,card.options.y_max)):
        if minimum is not None and maximum is not None and minimum >= maximum:
            raise HTTPException(422,"축 최솟값은 최댓값보다 작아야 합니다.")
    return compiled


def clear_cache():
    _chart_cache.clear()


def chart_query(card: CardSpec, context: QueryContext, *, dataset=None):
    # Internal callers (chat, exports) receive their own mutable object.
    return json.loads(_chart_payload(card, context, dataset=dataset))


def chart_response(card: CardSpec, context: QueryContext):
    # Rows are already JSON-safe. Sending immutable bytes avoids walking tens
    # of thousands of values again on FastAPI's shared HTTP event loop.
    return Response(_chart_payload(card, context), media_type="application/json")


def _chart_payload(card, context, *, dataset=None):
    if not isinstance(card, CardSpec):
        card = CardSpec.model_validate(card)
    if not isinstance(context, QueryContext):
        context = QueryContext.model_validate(context)
    # Resolve ownership before cache lookup; an omitted project may not widen an
    # owned dataset, and a mismatched project must not reuse a cached result.
    if dataset is not None:
        compiled = validate_card(card, dataset)
        card = card.model_copy(update={"dataset_version": compiled.get("dataset_version", card.dataset_version)})
        context = scoped_context(compiled["project_id"], context)
        return ChartPayload.from_result(_compute_chart(card, context, compiled)).fresh
    dataset, catalog = load_dataset(card.dataset_id, with_catalog=True)
    card = card.model_copy(update={"dataset_version": dataset.version})
    context = scoped_context(dataset.project_id, context)
    if card.kind == 'gantt':
        from .gantt import validate_gantt
        # Check all four sources before cache lookup, including hidden tables.
        validate_gantt(card, {'columns': dataset.columns, 'source': {'id': dataset.spec['source_id']}}, catalog)
    # Authorization/visibility and latest metadata are read on EVERY request.
    # A cache hit may skip SQL parsing only when all compilation inputs still
    # match, including source schema edits without a dataset version change.
    schema = [sorted((source_json(s) for s in catalog.sources.values() if source_visible(s)), key=lambda s: s["id"]),
              sorted(({c.name: getattr(r, c.name) for c in Relation.__table__.columns}
                      for r in catalog.relations.values()), key=lambda r: r["id"])]
    from .project_settings import managed_project
    current_project = managed_project(context.project_id)
    project_scope_key = current_project.lot_cds if current_project else None
    cache_key = hashlib.sha256(json.dumps([database.workspace_token(), dataset_json(dataset), schema, project_scope_key,
        card.model_dump(), context.model_dump(exclude={"refresh", "cursor"})], sort_keys=True).encode()).hexdigest()
    payload, cached = _chart_cache.get_or_compute(cache_key,
        lambda: ChartPayload.from_result(_compute_chart(card, context, validate_card(card, dataset, catalog=catalog))),
        refresh=context.refresh)
    return payload.cached if cached else payload.fresh


def _compute_chart(card, context, compiled):
    if card.editor_mode == 'python':
        return _compute_script_card(card, context, compiled)
    if card.kind == 'gantt':
        from .gantt import compute_gantt
        return compute_gantt(card, context, compiled)
    started = time.monotonic()
    compiled, period = apply_period(compiled, card, context)
    cte, params, ignored = _dataset_cte(compiled, context, card.filters)
    columns = {c["name"] for c in compiled["columns"]}
    x = quote_output(card.x) if card.x else None
    y = quote_output(card.y) if card.y else None
    color = quote_output(card.color) if card.color else None
    trellis = quote_output(card.trellis) if card.trellis else None
    facet_select = f", {trellis} AS trellis" if trellis else ""
    if trellis:
        panels = execute_rows(f"{cte} SELECT DISTINCT {trellis} AS trellis FROM filtered LIMIT 13", params)
        if len(panels) > 12:
            raise HTTPException(422, "Trellis는 최대 12개 패널을 표시합니다. 필터를 좁히거나 다른 구분 컬럼을 선택하세요.")
    expression_sql = None
    if "card_expression" in compiled:
        expression_sql, expression_params = compiled["card_expression"]
        params.update(expression_params)
        if card.kind not in {"wafer"}:
            y = expression_sql
    sampled, truncated, mode = False, False, "aggregate"
    value_label = None
    cap = settings.max_render_points if card.renderer == "seaborn" else settings.max_scatter_points
    agg = card.aggregate
    aggregate_sql = ("100.0 * SUM(passed_dies) / NULLIF(SUM(tested_dies), 0)" if agg == "yield" else
                     "COUNT(*)" if agg in {"count", "none"} else f"{agg.upper()}({y})")
    if expression_sql is not None:
        aggregate_sql = expression_sql
        value_label = card.value_expression_name or "직접 계산식"
    if card.kind == "kpi":
        sql = f"{cte} SELECT {aggregate_sql} AS value FROM filtered"
    elif card.kind in {"bar", "line"} and agg != "none":
        groups = [x] + ([color] if color else []) + ([trellis] if trellis else [])
        sql = f"{cte} SELECT {x} AS x, {aggregate_sql} AS y" + (f", {color} AS color" if color else "") + facet_select
        sql += f" FROM filtered GROUP BY {', '.join(groups)} ORDER BY x {card.options.sort.upper()} NULLS LAST LIMIT {MAX_AGGREGATES + 1}"
    elif card.kind == "histogram":
        # Width-bucket server aggregation avoids transferring every input value.
        bins = card.options.bins
        params["bins"] = bins
        facet_bucket = f", f.{trellis} AS trellis" if trellis else ""
        facet_group = ", trellis" if trellis else ""
        sql = f"{cte}, bounds AS (SELECT MIN({x})::double precision lo, MAX({x})::double precision hi FROM filtered), buckets AS (SELECT CASE WHEN b.hi=b.lo THEN 1 ELSE LEAST(width_bucket(f.{x}::double precision,b.lo,b.hi,:bins),:bins) END bucket, b.lo, b.hi{facet_bucket} FROM filtered f CROSS JOIN bounds b WHERE f.{x} IS NOT NULL) SELECT MIN(lo)+(bucket-0.5)*CASE WHEN MAX(hi)=MIN(lo) THEN 0 ELSE (MAX(hi)-MIN(lo))/:bins END AS x, COUNT(*) AS y{facet_group} FROM buckets GROUP BY bucket{facet_group} ORDER BY bucket{facet_group}"
    elif card.kind in {"wafer"}:
        # Coordinates from distinct wafers must never overlap into a misleading map.
        if "wafer_id" not in columns:
            raise HTTPException(422, "Wafer map에는 wafer_id가 필요합니다.")
        if trellis:
            mixed_panels = execute_rows(
                f"{cte} SELECT trellis FROM (SELECT DISTINCT {trellis} AS trellis, wafer_id "
                "FROM filtered) AS panel_wafers GROUP BY trellis HAVING COUNT(*) > 1 LIMIT 1", params)
            if mixed_panels:
                raise HTTPException(422, "각 패널에는 하나의 wafer만 표시할 수 있습니다. 패널 구분을 wafer_id로 선택하거나 Wafer 필터를 좁혀주세요.")
        else:
            wafer_ids = execute_rows(f"{cte} SELECT DISTINCT wafer_id FROM filtered LIMIT 2", params)
            if len(wafer_ids) > 1:
                raise HTTPException(422, "Wafer map은 Wafer 필터로 하나의 wafer를 선택하세요.")
        if expression_sql is not None:
            val = expression_sql
        elif agg == "none" and not color:
            # Preserve the automatic value used by existing Wafer cards.
            val = "CASE WHEN COUNT(*) FILTER (WHERE status='tested')=0 THEN NULL ELSE 100.0*COUNT(*) FILTER (WHERE status='tested' AND passed)/COUNT(*) FILTER (WHERE status='tested') END" if {"passed", "status"}.issubset(columns) else "COUNT(*)"
        elif agg == "yield":
            val = aggregate_sql
        elif agg == "count":
            val = "COUNT(*)"
        else:
            fn = "AVG" if agg == "none" else agg.upper()
            val = f"{fn}(CAST({color} AS double precision))"
        facet_group = f", {trellis}" if trellis else ""
        sql = f"{cte} SELECT {x} AS x, {y} AS y, {val} AS value{facet_select} FROM filtered GROUP BY {x}, {y}{facet_group} ORDER BY x,y{facet_group} LIMIT {cap+1}"
        value_label = value_label or ("검사 수율 (%)" if card.kind == "wafer" and agg == "none" and not color and {"passed", "status"}.issubset(columns) else
                       "수율 (%)" if agg == "yield" else "셀별 행 개수" if agg == "count" or not color else card.color)
    else:
        mode = "full"
        # Deterministic, indexed-prefix sample. This bounded query is intentionally not
        # advertised as random/representative; charts expose the method to the user.
        sql = f"{cte} SELECT {x} AS x, {y} AS y" + (f", {color} AS color" if color else "") + facet_select
        order = f'{x} {card.options.sort.upper()} NULLS LAST, "__row_id"' if card.kind in {"bar","line"} else '"__row_id"'
        sql += f' FROM filtered ORDER BY {order} LIMIT {cap+1}'
    rows = execute_rows(sql, params)
    if (card.kind in {"scatter", "box"} or (card.kind in {"bar", "line"} and agg == "none")) and len(rows) > cap:
        rows, sampled, mode = rows[:cap], True, "sample"
    if card.kind in {"bar", "line"} and agg != "none" and len(rows) > MAX_AGGREGATES:
        rows, truncated = rows[:MAX_AGGREGATES], True
    if card.kind in {"wafer"} and len(rows) > cap:
        rows, truncated = rows[:cap], True
    result = {"rows": rows, "meta": {"mode": mode, "returned_rows": len(rows),
              "dataset_version": card.dataset_version,
              "trellis": card.trellis,
              "queried_at": datetime.now(timezone.utc).isoformat(), "cached": False,
              "ignored_filters": ignored, "sampled": sampled,
              "sample_method": ("sort_prefix" if card.kind in {"bar","line"} else "primary_key_prefix") if sampled else None,
              "truncated": truncated, "aggregate": agg, "duration_ms": round((time.monotonic()-started)*1000, 2)}}
    if value_label is not None:
        result["meta"]["value_label"] = value_label
    result["meta"]["period"] = period
    return result


def _compute_script_card(card, context, compiled):
    from .plotly_scripts import execute_script
    started = time.monotonic()
    script = card.python_script
    if card.script_input == 'chart':
        # Backward compatibility uses the same visible migration as the editor;
        # all workers receive the dataset's original schema, never SQL x/y aliases.
        from .plotly_codegen import expanded_dataset_script
        script, _ = expanded_dataset_script(card.model_dump(), compiled['columns'])
    compiled, period = apply_period(compiled, card, context)
    cte, params, ignored = _dataset_cte(compiled, context, card.filters, include_row_id=False)
    names = ', '.join(quote_output(c['name']) for c in compiled['columns'])
    columns = compiled['columns']
    # Dataset SQL/script owns ordering. Avoid an expensive synthetic row-number
    # sort of the entire result; no row cap, sample, or aggregate is added.
    rows = stream_rows(f'{cte} SELECT {names} FROM filtered', params)
    execution = execute_script(script, rows, columns)
    base = {'rows':[], 'meta':{'mode':'full', 'returned_rows':execution.input_rows,
            'input_rows':execution.input_rows, 'input_bytes':execution.input_bytes,
            'load_ms':execution.load_ms, 'execution_ms':execution.execution_ms,
            'queried_at':datetime.now(timezone.utc).isoformat(), 'cached':False, 'ignored_filters':ignored,
            'dataset_version':card.dataset_version, 'sampled':False, 'truncated':False,
            'sample_method':None, 'period':period,
            'duration_ms':round((time.monotonic()-started)*1000,2)}}
    # Browser and cache retain only the figure, never a second copy of the raw df.
    return {**base,'figure':execution.figure,'meta':{**base['meta'],'script_input':'dataset','editor_mode':'python'}}
