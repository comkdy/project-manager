"""A scalar SQL expression compiler. Never accepts a SELECT or arbitrary function."""
import re
from decimal import Decimal

import sqlglot
from fastapi import HTTPException
from sqlglot import exp

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,159}$")
NODE_NAMES = {
    "Column", "Identifier", "Literal", "Null", "Boolean", "Paren", "Neg", "Add", "Sub",
    "Mul", "Div", "Mod", "EQ", "NEQ", "GT", "GTE", "LT", "LTE", "And", "Or", "Not",
    "Is", "Between", "In", "Like", "ILike", "Case", "If", "Cast", "DataType", "DataTypeParam",
    "Abs", "Round", "Floor", "Ceil", "Lower", "Upper", "Length", "Trim", "Coalesce",
    "Nullif", "Greatest", "Least", "Concat", "TimestampTrunc", "DateTrunc", "Var",
}
SAFE_TYPES = {"INT", "BIGINT", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "DECIMAL", "TEXT",
              "VARCHAR", "CHAR", "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMPTZ"}
SAFE_UNITS = {"DAY", "WEEK", "MONTH", "QUARTER", "YEAR", "HOUR", "MINUTE", "SECOND"}


def quote(name: str) -> str:
    if not IDENTIFIER.fullmatch(name):
        raise HTTPException(422, "지원하지 않는 SQL 식별자입니다.")
    return '"' + name + '"'


def quote_output(name: str) -> str:
    """Quote result labels independently of the stricter physical catalog names."""
    if not name.strip() or len(name.encode('utf-8')) > 63 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise HTTPException(422, "출력 이름은 제어 문자 없이 63바이트 이내여야 합니다. 긴 표현식에는 짧은 출력 이름을 지정하세요.")
    # SQLAlchemy text() also scans quoted identifiers for :bind placeholders.
    return '"' + name.replace('"', '""').replace(':', '\\:') + '"'


def compile_expression(expression: str, columns: set[str], prefix: str = "expr", *, aggregates=False) -> tuple[str, dict]:
    if not expression.strip() or len(expression) > 4000:
        raise HTTPException(422, "SQL 표현식은 1~4000자여야 합니다.")
    try:
        statements = sqlglot.parse(expression, read="postgres")
    except (sqlglot.errors.SqlglotError, RecursionError):
        raise HTTPException(422, "SQL 표현식 문법이 올바르지 않습니다.") from None
    if len(statements) != 1 or statements[0] is None:
        raise HTTPException(422, "하나의 SQL 표현식만 입력하세요.")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > 250:
        raise HTTPException(422, "SQL 표현식이 너무 복잡합니다.")
    for node in nodes:
        allowed = NODE_NAMES | ({"Count", "Sum", "Avg", "Min", "Max", "Distinct", "Star", "Filter", "Where"} if aggregates else set())
        if type(node).__name__ not in allowed:
            raise HTTPException(422, f"허용되지 않은 SQL 구문/함수입니다: {type(node).__name__}")
        if isinstance(node, exp.AggFunc) and node.find_ancestor(exp.AggFunc):
            raise HTTPException(422, "집계 함수 안에 집계 함수를 중첩할 수 없습니다.")
        if isinstance(node, exp.Star) and (not isinstance(node.parent, exp.Count) or any(node.args.values())):
            raise HTTPException(422, "집계식의 *는 COUNT(*)에서만 사용할 수 있습니다.")
        if isinstance(node, exp.Distinct) and (not isinstance(node.parent, exp.Count) or len(node.expressions) != 1):
            raise HTTPException(422, "DISTINCT는 COUNT(DISTINCT 단일 표현식)에서만 지원합니다.")
        if isinstance(node, exp.Filter):
            if not isinstance(node.this, exp.AggFunc) or not isinstance(node.expression, exp.Where) or node.expression.find(exp.AggFunc):
                raise HTTPException(422, "집계 FILTER에는 행 조건을 지정하세요.")
        if node.comments:
            raise HTTPException(422, "SQL 주석은 지원하지 않습니다.")
        if isinstance(node, exp.Column):
            if node.table or node.db or node.catalog or node.name not in columns:
                raise HTTPException(422, f"등록되지 않은 컬럼입니다: {node.name}")
            node.set("this", exp.Identifier(this=node.name, quoted=True))
        if isinstance(node, exp.DataType):
            if node.this.name not in SAFE_TYPES or node.args.get("nested"):
                raise HTTPException(422, "지원하지 않는 CAST 타입입니다.")
            for parameter in node.expressions:
                try:
                    value = int(parameter.this.this)
                except (ValueError, TypeError, AttributeError):
                    raise HTTPException(422, "CAST 타입 크기가 올바르지 않습니다.") from None
                if not 0 <= value <= 1000:
                    raise HTTPException(422, "CAST 타입 크기는 0~1000 범위여야 합니다.")
        if isinstance(node, exp.Var) and str(node.this).upper() not in SAFE_UNITS:
            raise HTTPException(422, "지원하지 않는 날짜 단위입니다.")
    params = {}
    # Literal values never become query text. Type modifiers must remain SQL literals.
    for node in nodes:
        if isinstance(node, exp.Literal):
            if len(str(node.this)) > 2000:
                raise HTTPException(422, "SQL 상수가 너무 큽니다.")
            if isinstance(node.parent, exp.DataTypeParam):
                continue
            key = f"{prefix}_{len(params)}"
            literal = str(node.this)
            params[key] = literal if node.is_string else (int(literal) if re.fullmatch(r"[0-9]+", literal) else Decimal(literal))
            parameter = exp.Var(this=f":{key}")
            if node is tree:
                tree = parameter
            else:
                node.replace(parameter)
    return tree.sql(dialect="postgres"), params
