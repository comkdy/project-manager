"""Validate read queries without rewriting the SQL submitted to PostgreSQL."""
import sqlglot
from fastapi import HTTPException
from sqlglot import exp, tokens
from sqlglot.optimizer.scope import Scope, traverse_scope

from .raw_sql import MAX_SQL_LENGTH, MAX_SQL_NODES


# Anonymous calls are the escape hatch for PostgreSQL extension/UDF functions.
# Keep these to ordinary scalar helpers; stateful/admin/dynamic-SQL functions
# (set_config, nextval, dblink_exec, query_to_xml, lo_import, etc.) are excluded.
SAFE_ANONYMOUS_FUNCTIONS = frozenset("""
    age date_bin make_date make_time make_timestamp make_timestamptz make_interval
    justify_days justify_hours justify_interval isfinite to_char to_date to_number
    format initcap quote_ident quote_literal quote_nullable ascii chr
    regexp_count regexp_instr regexp_like regexp_substr regexp_match regexp_matches
    split_part string_to_array array_to_string cardinality array_length array_lower
    array_upper array_ndims array_dims array_position array_positions array_remove
    array_replace array_cat array_append array_prepend trim_array
    json_typeof jsonb_typeof json_array_length jsonb_array_length
    json_build_object jsonb_build_object json_build_array jsonb_build_array
    to_json to_jsonb json_extract_path jsonb_extract_path
    json_extract_path_text jsonb_extract_path_text
    pg_typeof pg_column_size encode decode convert_from convert_to
    width_bucket div gcd lcm scale min_scale trim_scale
""".split())


def parse_query(sql):
    if not sql.strip() or len(sql) > MAX_SQL_LENGTH:
        raise HTTPException(422, "SQL은 1~20,000자여야 합니다.")
    try:
        statements = [node for node in sqlglot.parse(sql, read="postgres")
                      if node is not None and not isinstance(node, exp.Semicolon)]
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
            raise HTTPException(422, "하나의 조회용 SELECT 또는 WITH 쿼리를 입력하세요.")
        tree = statements[0]
        nodes = list(tree.walk())
        if len(nodes) > MAX_SQL_NODES:
            raise HTTPException(422, "SQL이 너무 복잡합니다.")
        if any(isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)) for node in nodes):
            raise HTTPException(422, "조회 SQL에서는 데이터 변경이나 잠금을 사용할 수 없습니다.")
        for node in nodes:
            if isinstance(node, exp.Anonymous) and node.name.lower() not in SAFE_ANONYMOUS_FUNCTIONS:
                raise HTTPException(422, f"조회용으로 허용되지 않은 함수입니다: {node.name}")
            if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func) and node.this.name != "pg_catalog":
                raise HTTPException(422, "사용자 정의·외부 함수는 조회 SQL에서 실행할 수 없습니다.")
        return tree
    except (sqlglot.errors.SqlglotError, RecursionError):
        raise HTTPException(422, "SQL 문법을 확인하세요.") from None


def referenced_sources(tree, catalog):
    found = {}
    for scope in traverse_scope(tree):
        try:
            selected = scope.selected_sources.values()
        except sqlglot.errors.SqlglotError:
            raise HTTPException(422, "테이블 별칭이 중복되거나 모호합니다.") from None
        for node, source in selected:
            if isinstance(source, Scope):
                continue
            if not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier):
                raise HTTPException(422, "FROM에는 등록된 테이블이나 조회 서브쿼리를 사용하세요.")
            if source.catalog:
                raise HTTPException(422, "외부 카탈로그는 조회할 수 없습니다.")
            name = source.name if source.this.args.get("quoted") else source.name.lower()
            schema_node = source.args.get("db")
            schema = (source.db if schema_node.args.get("quoted") else source.db.lower()) if schema_node else None
            matches = [item for item in catalog if item["table_name"] == name and item["schema_name"] == (schema or "public")]
            identities = {(item["schema_name"], item["table_name"]) for item in matches}
            if len(identities) != 1:
                raise HTTPException(422, f"등록되지 않았거나 모호한 테이블입니다: {source.sql()}")
            found[next(iter(identities))] = matches[0]
    return list(found.values())


def embedded_query(sql):
    """Only strip the statement terminator when embedding a saved query."""
    lexical = sqlglot.tokenize(sql, read="postgres")
    for token in reversed(lexical):
        if token.token_type == tokens.TokenType.SEMICOLON:
            sql = sql[:token.start] + sql[token.end + 1:]
    # A newline keeps a final line comment from consuming the wrapper.
    return sql + "\n"


def sqlalchemy_literal(sql):
    # text() recognizes :parameters even inside quoted SQL strings. The user
    # query has no application bind parameters; keep every colon literal.
    return sql.replace(":", "\\:")
