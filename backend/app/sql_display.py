"""Readable SQL generation; never applied to SQL authored in the SQL editor."""
import re

from sqlalchemy.dialects.postgresql.base import RESERVED_WORDS
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres


class _ReadablePostgresGenerator(Postgres.Generator):
    def not_sql(self, expression):
        predicate = expression.this
        # Spell the form's literal exclusion lists as `column NOT IN (...)`.
        # Only AST nodes are rendered; never replace text inside SQL literals.
        if isinstance(predicate, exp.In) and predicate.expressions and not any(
            predicate.args.get(key) for key in ('query', 'unnest', 'field', 'is_global')
        ):
            values = self.expressions(predicate, dynamic=True, new_line=True, skip_first=True, skip_last=True)
            return f"{self.sql(predicate, 'this')} NOT IN ({values})"
        return super().not_sql(expression)


def generated_sql(query, *, quote_all=False):
    query = query.copy()
    for identifier in query.find_all(exp.Identifier):
        name = identifier.name
        ordinary = re.fullmatch(r"[a-z_][a-z0-9_$]*", name) and name not in RESERVED_WORDS
        identifier.set("quoted", quote_all or not ordinary)
    return _ReadablePostgresGenerator(dialect=Postgres, pretty=True).generate(query)
