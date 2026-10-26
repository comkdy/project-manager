"""The simple editor is a lossless subset of the SQL editor."""
from fastapi import HTTPException
from sqlglot import exp

from .direct_sql import parse_query
from .editor_filters import editable_filters
from .raw_sql import compile_raw_sql, form_to_sql
from .schemas import DatasetSpec


def simple_editor_state(sql, source, pivot=None):
    try:
        if pivot is not None:
            from .pivot import pivot_sql
            if sql == pivot_sql(pivot, source):
                return {'available': True, 'reason': None,
                        'spec': DatasetSpec(source_id=source['id'], pivot=pivot).model_dump()}
        tree = parse_query(sql)
        if not isinstance(tree, exp.Select):
            raise HTTPException(422, "UNION·INTERSECT·EXCEPT")
        labels = {"joins": "JOIN", "having": "HAVING", "with_": "WITH/CTE", "order": "ORDER BY",
                  "limit": "LIMIT", "offset": "OFFSET", "distinct": "DISTINCT", "windows": "WINDOW"}
        unsupported = [labels.get(key, key.upper()) for key, value in tree.args.items()
                       if value and key not in {"expressions", "from_", "where", "group"}]
        if unsupported:
            raise HTTPException(422, "·".join(unsupported))
        if any(isinstance(node, (exp.Subquery, exp.Window, exp.Select)) and node is not tree for node in tree.walk()):
            raise HTTPException(422, "인라인 뷰·서브쿼리·윈도 함수")
        compiled = compile_raw_sql(tree.sql(dialect="postgres", comments=False), source)
        # Decode WHERE against input columns, even for grouped queries.
        spec = editable_filters(compiled["spec"], include_grouped=True, input_columns=source["columns"])
        return {"available": True, "reason": None, "spec": spec.model_dump()}
    except (HTTPException, ValueError, RecursionError) as error:
        detail = error.detail if isinstance(error, HTTPException) else "지원 범위를 벗어난 SQL"
        return {"available": False, "reason": f"간편 작성기로 표현할 수 없습니다: {str(detail).rstrip('. ')}. SQL 편집을 사용하세요."}


def prepare_simple_spec(spec, source):
    if spec.raw_sql is not None or spec.joins or spec.having_sql or spec.pivot:
        raise HTTPException(422, "간편 작성기는 단일 테이블의 SELECT·FROM·WHERE·GROUP BY만 지원합니다.")
    # Build WHERE filters using input fields, independently of the projection
    # and aggregation. Never turn a simple filter into HAVING.
    filter_spec = DatasetSpec(source_id=spec.source_id, filters=spec.filters)
    filter_sql = form_to_sql(filter_spec, source)
    predicate = parse_query(filter_sql).args.get("where")
    conditions = []
    if spec.where_sql:
        import sqlglot
        conditions.append(sqlglot.parse_one(spec.where_sql, read="postgres"))
    if predicate:
        condition = predicate.this.copy()
        for column in condition.find_all(exp.Column):
            column.set("table", None)
        conditions.append(condition)
    return spec.model_copy(update={"filters": [], "where_sql": exp.and_(*conditions).sql(dialect="postgres") if conditions else None})
