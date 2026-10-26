"""Compile typed arithmetic tokens, never user-supplied SQL or function names."""
from decimal import Decimal

from fastapi import HTTPException

from .expressions import quote_output

NUMERIC_TYPES = {"smallint", "integer", "bigint", "numeric", "real", "double precision", "decimal"}
EXPRESSION_KINDS = {"kpi", "bar", "line", "scatter", "box", "wafer"}


def expression_label(tokens):
    return " ".join(t.name if t.kind == "column" else {"*": "×", "/": "÷"}.get(t.value, t.value) for t in tokens)


def compile_card_expression(card, columns):
    tokens = card.value_expression
    if card.kind not in EXPRESSION_KINDS or card.aggregate not in {"sum", "avg", "min", "max", "none"}:
        raise HTTPException(422, "이 차트 또는 계산 방식에는 직접 계산식을 사용할 수 없습니다.")
    if card.kind == "kpi" and card.aggregate == "none":
        raise HTTPException(422, "KPI 계산식은 컬럼 집계 방식이 필요합니다.")
    if card.kind in {"scatter", "box"} and card.aggregate != "none":
        raise HTTPException(422, "산점도와 상자 차트의 계산식은 각 행의 값을 사용합니다.")
    if not tokens or len(tokens) > 100:
        raise HTTPException(422, "계산식에 컬럼과 사칙연산을 입력하세요. 최대 100개 항목을 사용할 수 있습니다.")
    types = {c['name']: c['type'] for c in columns}
    position, column_count, params = 0, 0, {}
    aggregate = "avg" if card.kind in {"wafer"} and card.aggregate == "none" else card.aggregate

    def fail():
        raise HTTPException(422, "계산식의 컬럼·숫자·연산자 순서와 괄호를 확인하세요.")

    def atom(depth):
        nonlocal position, column_count
        if position >= len(tokens) or depth > 64:
            fail()
        token = tokens[position]
        position += 1
        if token.kind == "column":
            if types.get(token.name) not in NUMERIC_TYPES:
                raise HTTPException(422, f"계산식에는 현재 데이터셋의 숫자형 컬럼만 사용할 수 있습니다: {token.name}")
            column_count += 1
            value = f"CAST({quote_output(token.name)} AS numeric)"
            return value if aggregate == "none" else f"{aggregate.upper()}({value})"
        if token.kind == "number":
            key = f"card_expr_{len(params)}"
            params[key] = Decimal(token.value)
            return f"CAST(:{key} AS numeric)"
        if token.value in {"+", "-"}:
            return f"({token.value}{atom(depth + 1)})"
        if token.value == "(":
            value = expression(0, depth + 1)
            if position >= len(tokens) or tokens[position].kind != "operator" or tokens[position].value != ")":
                fail()
            position += 1
            return value
        fail()

    def expression(min_precedence, depth):
        nonlocal position
        left = atom(depth)
        precedence = {"+": 1, "-": 1, "*": 2, "/": 2}
        while position < len(tokens):
            token = tokens[position]
            level = precedence.get(token.value, 0) if token.kind == "operator" else 0
            if level == 0 or level < min_precedence:
                break
            position += 1
            right = expression(level + 1, depth + 1)
            left = f"({left} / NULLIF({right}, 0))" if token.value == "/" else f"({left} {token.value} {right})"
        return left

    sql = expression(0, 0)
    if position != len(tokens):
        fail()
    if not column_count:
        raise HTTPException(422, "계산식에 데이터셋 컬럼을 하나 이상 추가하세요.")
    return sql, params
