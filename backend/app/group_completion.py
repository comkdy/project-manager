"""Suggest GROUP BY expressions for the simple editor without executing SQL."""
import sqlglot
from fastapi import HTTPException
from sqlglot import exp

from .expressions import compile_expression


def complete_groups(expressions: list[str], input_columns: list[str], having_sql: str | None = None) -> dict:
    names = set(input_columns)

    def parse(expression):
        compile_expression(expression, names, aggregates=True)
        tree = sqlglot.parse_one(expression, read="postgres")
        for column in tree.find_all(exp.Column):
            column.set("this", exp.to_identifier(column.name, quoted=True))
        return tree

    try:
        trees = [parse(expression) for expression in expressions]
        having = parse(having_sql) if having_sql and having_sql.strip() else None
    except (HTTPException, sqlglot.errors.SqlglotError, RecursionError):
        # Partial expressions are expected while typing. Never replace the last
        # valid groups with a partial guess.
        return {"complete": False, "group_by": []}

    if not having and not any(tree.find(exp.AggFunc) for tree in trees):
        return {"complete": True, "group_by": []}

    groups = {}

    def key(node):
        while isinstance(node, exp.Paren):
            node = node.this
        return node.sql(dialect="postgres")

    def add(node):
        groups.setdefault(key(node), None)

    # Keep whole nonaggregate expressions (e.g. DATE_TRUNC), not their inputs.
    for tree in trees:
        if not tree.find(exp.AggFunc) and tree.find(exp.Column):
            add(tree)

    def collect(node, *, predicate=False):
        if isinstance(node, (exp.AggFunc, exp.Filter)) or key(node) in groups:
            return
        if not node.find(exp.Column):
            return
        if not node.find(exp.AggFunc) and (not predicate or isinstance(node, exp.Column)):
            add(node)
            return
        for child in node.iter_expressions():
            collect(child, predicate=predicate)

    for tree in trees:
        collect(tree)
    if having is not None:
        collect(having, predicate=True)
    if len(groups) > 30:
        raise HTTPException(422, "자동 group은 최대 30개까지 지원합니다. column을 줄이거나 group을 직접 지정하세요.")
    return {"complete": True, "group_by": list(groups)}
