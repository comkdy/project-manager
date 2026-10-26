"""Conservative equality: identical source + PostgreSQL syntax tree, same scope.

Only comments/formatting are ignored. Predicates, joins, aggregation, ordering,
limits and literal values remain significant. No guessed semantic equivalence.
"""
from fastapi import HTTPException
from .direct_sql import parse_query


def query_key(spec):
    sql = spec.get('raw_sql')
    if not sql: return None
    try:
        return spec['source_id'], parse_query(sql).sql(dialect='postgres',normalize=True,comments=False)
    except HTTPException:
        return None


def reusable_dataset(definition, candidates, project_id):
    key = query_key(definition['spec'])
    if key is None: return None
    eligible = [d for d in candidates if d['project_id'] == project_id and query_key(d['spec'])==key]
    return min(eligible,key=lambda d:(d['project_id']!=project_id,d['id'])) if eligible else None
