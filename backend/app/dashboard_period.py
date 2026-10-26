"""Source predicates for dashboard periods and project execution boundaries.

Authored dataset SQL is never overwritten.

Filter a physical source before its projection, joins and aggregates, including
inside CTEs. A selected view is filtered at the view boundary; PostgreSQL decides
whether predicates can be pushed into the view's own definition.
"""
from datetime import date
import re

from fastapi import HTTPException
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .direct_sql import parse_query
from .raw_sql import form_to_sql


def period_columns(compiled):
    sources = compiled.get("sources")
    if sources is None:
        sources = [compiled["source"], *(target for _, target in compiled["relations"])]
    candidates = [dict(source_id=s["id"], column=c["name"], type=c["type"],
                       label=f'{s["schema_name"]}.{s["table_name"]}.{c["name"]}')
                  for s in sources for c in s["columns"]
                  if re.fullmatch(r"date|timestamptz(?:\(\d+\))?|timestamp(?:\(\d+\))?(?: with(?:out)? time zone)?", c["type"].lower())]
    primary = [c for c in candidates if c["source_id"] == compiled["source"]["id"]]
    default = primary[0] if len(primary) == 1 else candidates[0] if len(candidates) == 1 else None
    pivot = getattr(compiled.get('spec'), 'pivot', None)
    if pivot is not None:
        default = next((c for c in primary if c['column'] == pivot.time_column), default)
    return {"columns": candidates, "default": default}


def _identifier(node):
    return node.name if node.args.get("quoted") else node.name.lower()


def filter_source(sql, source, column, start):
    monday = date.fromisocalendar(start.year, start.week, 1).isoformat()
    dtype = column["type"].lower()
    aware = dtype.startswith("timestamptz") or "with time zone" in dtype
    value = monday + "T00:00:00+09:00" if aware else monday
    cast = exp.Cast(this=exp.Literal.string(value), to=exp.DataType.build(
        "timestamptz" if aware else "date" if dtype == "date" else "timestamp", dialect="postgres"))
    predicate = exp.GTE(this=exp.column(column["column"], quoted=True), expression=cast)
    return filter_source_predicate(sql, source, predicate)


def filter_source_predicate(sql, source, predicate):
    """Scope every physical occurrence before joins/aggregates, preserving CTE names."""
    tree = parse_query(sql)
    selected = []
    for scope in traverse_scope(tree):
        for _, table in scope.selected_sources.values():
            if isinstance(table, Scope) or not isinstance(table, exp.Table):
                continue
            schema = _identifier(table.args["db"]) if table.args.get("db") else "public"
            if schema == source["schema_name"] and _identifier(table.this) == source["table_name"]:
                selected.append((scope, table))
    if not selected:
        raise HTTPException(422, "범위 기준 테이블이 현재 쿼리에 없습니다. 조회 조건을 확인하세요.")
    for scope, table in selected:
        physical = table.copy()
        physical.set("alias", None)
        alias = table.args.get("alias")
        alias = alias.copy() if alias else exp.TableAlias(this=table.this.copy())
        # schema.table.column becomes table.column once the table is wrapped.
        # Include correlated references, but never same-named tables in other scopes.
        if not table.args.get("alias"):
            for reference in [*scope.columns, *scope.stars]:
                if reference.db == table.db and reference.table == table.name:
                    reference.set("db", None)
                    reference.set("catalog", None)
        table.replace(exp.Subquery(this=exp.select("*").from_(physical).where(predicate.copy()), alias=alias))
    return tree.sql(dialect="postgres")


def apply_period(compiled, card, context):
    if not card.period.use_dashboard:
        return compiled, None
    start = context.period_start
    if start is None:
        return compiled, None
    choices = period_columns(compiled)
    chosen = (next((c for c in choices["columns"] if c["source_id"] == card.period.source_id
                    and c["column"] == card.period.column), None)
              if card.period.column else choices["default"])
    if chosen is None:
        message = ("날짜·시간 컬럼이 없어 기간을 적용할 수 없습니다." if not choices["columns"] else
                   "카드 세부 설정 > 필터에서 기간 기준 컬럼을 선택하세요.")
        raise HTTPException(422, message)
    sources = compiled.get("sources") or [compiled["source"], *(t for _, t in compiled["relations"])]
    source = next(s for s in sources if s["id"] == chosen["source_id"])
    metadata = {"start": start.model_dump(), "source_id": chosen["source_id"],
                "column": chosen["column"], "label": chosen["label"], "timezone": "Asia/Seoul"}
    if "direct_sql" not in compiled:
        # Preserve the form compiler's primary key and indexed prefix order.
        # A raw-SQL conversion introduced ROW_NUMBER and a full sort here.
        dtype = chosen["type"].lower()
        aware = dtype.startswith("timestamptz") or "with time zone" in dtype
        monday = date.fromisocalendar(start.year, start.week, 1).isoformat()
        predicate = {"schema_name": source["schema_name"], "table_name": source["table_name"],
                     "column": chosen["column"], "value": monday + "T00:00:00+09:00" if aware else monday,
                     "cast": "timestamptz" if aware else "date" if dtype == "date" else "timestamp"}
        return {**compiled, "source_period": predicate}, metadata
    sql = compiled.get("direct_sql") or form_to_sql(compiled["spec"], compiled["source"],
                                                    context.project_id, compiled["relations"])
    filtered = filter_source(sql, source, chosen, start)
    return {**compiled, "direct_sql": filtered}, metadata
