"""Expose simple row predicates as editable filters without changing their meaning."""
import math
from decimal import Decimal

import sqlglot
from sqlglot import exp

from .schemas import Filter


def _unparen(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _terms(node):
    node = _unparen(node)
    if isinstance(node, exp.And):
        return _terms(node.this) + _terms(node.expression)
    return [node]


def _scalar(node):
    node = _unparen(node)
    if isinstance(node, exp.Boolean):
        return node.this
    if isinstance(node, exp.Literal) and node.is_string:
        return node.this
    negative = isinstance(node, exp.Neg)
    number = node.this if negative else node
    if not isinstance(number, exp.Literal) or number.is_string:
        raise ValueError()
    literal = ('-' if negative else '') + number.this
    decimal = Decimal(literal)
    value = int(decimal) if decimal == decimal.to_integral_value() else float(decimal)
    # JSON numbers cannot carry arbitrarily precise SQL numeric literals.
    if abs(value) > 2**53 - 1 or not math.isfinite(value) or Decimal(str(value)) != decimal:
        return literal
    return value


def _contains(pattern):
    if not isinstance(pattern, exp.Literal) or not pattern.is_string:
        raise ValueError()
    value = pattern.this
    if not value.startswith('%') or not value.endswith('%'):
        raise ValueError()
    result, index = '', 1
    while index < len(value) - 1:
        char = value[index]
        if char == '\\':
            index += 1
            if index >= len(value) - 1:
                raise ValueError()
            char = value[index]
        elif char in '%_':
            raise ValueError()
        result += char
        index += 1
    if len(result) > 2000:
        raise ValueError()
    return result


def editable_filters(spec, *, include_grouped=False, input_columns=None):
    """Move only representable, non-grouped WHERE terms to output filters.

    OR, NULL predicates, nonliteral IN lists, unprojected fields and grouped input conditions stay
    in SQL. In particular, a pre-aggregation WHERE must never become HAVING.
    """
    if not spec.where_sql:
        return spec
    outputs = ([(c["name"], exp.column(c["name"], quoted=True)) for c in input_columns] if input_columns is not None
               else [(c.name, sqlglot.parse_one(c.expression, read='postgres')) for c in spec.columns])
    if not include_grouped and (spec.group_by or spec.having_sql or any(tree.find(exp.AggFunc) for _, tree in outputs)):
        return spec
    operators = {exp.EQ:'eq', exp.NEQ:'ne', exp.GT:'gt', exp.GTE:'gte', exp.LT:'lt', exp.LTE:'lte'}
    filters, remaining = list(spec.filters), []
    for term in _terms(sqlglot.parse_one(spec.where_sql, read='postgres')):
        try:
            if len(filters) >= 30:
                raise ValueError()
            predicate = term
            negated = isinstance(predicate, exp.Not)
            if negated:
                predicate = _unparen(predicate.this)
                if not isinstance(predicate, exp.In):
                    raise ValueError()
            expression = _unparen(predicate.this)
            if isinstance(predicate, exp.ILike):
                # FilterBuilder's contains operation searches the TEXT cast.
                if isinstance(expression, exp.Cast) and expression.to == exp.DataType.build('TEXT'):
                    expression = _unparen(expression.this)
                else:
                    raise ValueError()
                op, value = 'contains', _contains(predicate.expression)
            elif isinstance(predicate, exp.In):
                if predicate.args.get('query') or not predicate.expressions or len(predicate.expressions) > 100:
                    raise ValueError()
                op, value = 'not_in' if negated else 'in', [_scalar(item) for item in predicate.expressions]
            elif type(predicate) in operators:
                op, value = operators[type(predicate)], _scalar(predicate.expression)
            else:
                raise ValueError()
            name = next((name for name, tree in outputs if _unparen(tree) == expression), None)
            if name is None:
                raise ValueError()
            filters.append(Filter(column=name, op=op, value=value))
        except ValueError:
            remaining.append(term)
    return spec.model_copy(update={
        'filters': filters,
        'where_sql': exp.and_(*remaining).sql(dialect='postgres') if remaining else None,
    })
