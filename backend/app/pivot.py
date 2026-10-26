"""Saved pivot recipes compile to read-only SQL, never to database objects."""
import re

from fastapi import HTTPException
from sqlglot import exp

from .direct_sql import parse_query
from .schemas import DatasetSpec
from .sql_display import generated_sql


def numeric_type(dtype):
    return bool(re.fullmatch(r'(?:smallint|integer|bigint|int[248]?|numeric|decimal|real|double precision|float[48]?)(?:\([^)]*\))?', dtype.lower()))


def time_type(dtype):
    return bool(re.fullmatch(r'date|timestamptz(?:\(\d+\))?|timestamp(?:\(\d+\))?(?: with(?:out)? time zone)?', dtype.lower()))


def identifier(name):
    return exp.to_identifier(name, quoted=True).sql(dialect='postgres')


def pivot_sql(pivot, source):
    columns = {c['name']: c['type'] for c in source['columns']}
    for name in [*pivot.keys, pivot.operation_column, pivot.value_column, pivot.time_column, pivot.tie_breaker]:
        if name not in columns:
            raise HTTPException(422, f'피벗 컬럼이 원천 테이블에 없습니다: {name}')
    if not numeric_type(columns[pivot.value_column]):
        raise HTTPException(422, '값에는 숫자 컬럼을 선택하세요.')
    if not time_type(columns[pivot.time_column]):
        raise HTTPException(422, '시간 기준에는 날짜·시간 컬럼을 선택하세요.')
    # Preserve the existing simple editor's input filters and supported WHERE.
    from .simple_editor import prepare_simple_spec
    from .raw_sql import form_to_sql
    where = None
    if pivot.filters or pivot.where_sql:
        input_spec = prepare_simple_spec(DatasetSpec(source_id=source['id'], filters=pivot.filters, where_sql=pivot.where_sql), source)
        clause = parse_query(form_to_sql(input_spec, source)).args.get('where')
        if clause:
            where = clause.this.copy()
            for column in where.find_all(exp.Column):
                column.set('table', None)
    keys = ', '.join(identifier(key) for key in pivot.keys)
    operation, value, measured, tie = map(identifier, [pivot.operation_column, pivot.value_column, pivot.time_column, pivot.tie_breaker])
    internal_value = '__pivot_value'
    while internal_value in columns:
        internal_value += '_'
    literals = [exp.Literal.string(item.value).sql(dialect='postgres') for item in pivot.outputs]
    predicates = [f'{operation} IN ({", ".join(literals)})', f'{measured} IS NOT NULL',
                  *(f'{identifier(key)} IS NOT NULL' for key in pivot.keys)]
    if where is not None:
        predicates.append(f'({where.sql(dialect="postgres")})')
    outputs = [f'MAX({internal_value}) FILTER (WHERE {operation} = {literal}) AS {identifier(item.name)}'
               for item, literal in zip(pivot.outputs, literals)]
    complete = ' AND '.join(f'COUNT({internal_value}) FILTER (WHERE {operation} = {literal}) > 0' for literal in literals)
    sql = f'''WITH __pivot_last AS (
        SELECT DISTINCT {keys}, {operation},
            LAST_VALUE({value}) OVER (
                PARTITION BY {keys}, {operation}
                ORDER BY {measured} ASC, {tie} ASC NULLS FIRST
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            ) AS {internal_value}
        FROM {identifier(source['schema_name'])}.{identifier(source['table_name'])}
        WHERE {' AND '.join(predicates)}
    )
    SELECT {keys}, {', '.join(outputs)} FROM __pivot_last
    GROUP BY {keys}
    {'HAVING ' + complete if pivot.require_all else ''}
    ORDER BY {keys}'''
    rendered = generated_sql(parse_query(sql))
    parse_query(rendered)
    return rendered


def pivot_definition(spec, source):
    sql = pivot_sql(spec.pivot, source)
    if spec.raw_sql is not None and spec.raw_sql != sql:
        raise HTTPException(422, 'SQL과 피벗 설정이 일치하지 않습니다. 직접 수정한 SQL은 피벗 설정 없이 저장하세요.')
    return spec.model_copy(update={'raw_sql': sql})
