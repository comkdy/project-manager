"""Axis scale eligibility; scales change presentation, never query values."""
from fastapi import HTTPException


def validate_axis_scales(card, columns):
    numeric = {"smallint", "integer", "bigint", "numeric", "real", "double precision", "decimal"}
    numeric_names = {column["name"] for column in columns if column["type"] in numeric}
    for axis in ("x", "y"):
        if getattr(card.options, f"{axis}_scale") != "log":
            continue
        supported = card.kind in {"scatter", "line", "histogram", "bar", "box"}
        if axis == "x":
            supported = supported and card.kind in {"scatter", "line", "histogram"} and card.x in numeric_names
        else:
            supported = supported and (card.kind == "histogram" or card.value_expression is not None
                                       or card.aggregate in {"count", "yield"} or card.y in numeric_names)
        if not supported:
            raise HTTPException(422, f"{axis.upper()}축에는 로그 스케일을 사용할 수 없습니다.")
