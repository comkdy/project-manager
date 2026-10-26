"""Convert the form-compatible SQL subset and legacy form dataset definitions.

The SQL editor executes through direct_sql/query; it does not use this converter
to regenerate the submitted statement.
"""
import math
import re
from decimal import Decimal

import sqlglot
from fastapi import HTTPException
from pydantic import ValidationError
from sqlglot import exp

from .expressions import compile_expression, quote, quote_output
from .sql_display import generated_sql

MAX_SQL_LENGTH = 20000
MAX_SQL_NODES = 2000
MAX_OUTPUT_COLUMNS = 100


def _fail(message):
    raise HTTPException(422, message)


def _identifier(node, *, output=False):
    if not isinstance(node, exp.Identifier):
        _fail("SQL 식별자가 올바르지 않습니다.")
    value = node.name if node.args.get("quoted") else node.name.lower()
    (quote_output if output else quote)(value)
    return value


def _parse(sql):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_LENGTH:
        _fail("SQL은 1~20,000자여야 합니다.")
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except RecursionError:
        _fail("SQL 중첩이 너무 깊습니다. 괄호나 계산식을 단순하게 작성하세요.")
    except sqlglot.errors.SqlglotError:
        _fail("SQL 문법이 올바르지 않습니다. SELECT와 WHERE를 확인하세요.")
    if len(statements) != 1 or statements[0] is None:
        _fail("하나의 SELECT 문만 입력하세요.")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > MAX_SQL_NODES:
        _fail("SQL이 너무 복잡합니다. 계산식이나 조건을 줄여 주세요.")
    if any(node.comments for node in nodes):
        _fail("SQL 주석은 지원하지 않습니다.")
    if not isinstance(tree, exp.Select):
        _fail("데이터 조회용 단일 SELECT 문만 지원합니다. DDL/DML·UNION은 사용할 수 없습니다.")
    clause_names = {
        "with_": "WITH/CTE", "distinct": "DISTINCT", "group": "GROUP BY",
        "having": "HAVING", "joins": "JOIN", "order": "ORDER BY", "limit": "LIMIT",
        "offset": "OFFSET", "locks": "FOR UPDATE/SHARE", "into": "SELECT INTO",
        "qualify": "QUALIFY", "windows": "WINDOW", "connect": "CONNECT BY",
    }
    for key, value in tree.args.items():
        if key not in {"expressions", "from_", "where", "joins", "group", "having"} and value:
            _fail(f"{clause_names.get(key, key.upper())} 구문은 지원하지 않습니다.")
    if any(isinstance(node, (exp.Subquery, exp.Select)) and node is not tree for node in nodes):
        _fail("서브쿼리는 지원하지 않습니다.")
    return tree


def _source_alias(tree, source):
    from_node = tree.args.get("from_")
    if not isinstance(from_node, exp.From) or not isinstance(from_node.this, exp.Table):
        _fail("FROM에는 등록된 공통 테이블 하나를 지정하세요.")
    if any(value for key, value in from_node.args.items() if key != "this"):
        _fail("FROM에는 등록된 공통 테이블 하나만 사용할 수 있습니다.")
    table = from_node.this
    if any(value for key, value in table.args.items() if key not in {"this", "db", "alias"}):
        _fail("테이블 함수·샘플링·외부 카탈로그는 지원하지 않습니다.")
    if _identifier(table.this) != source["table_name"]:
        _fail("FROM 테이블이 선택한 등록 원본과 다릅니다.")
    if table.args.get("db") and _identifier(table.args["db"]) != source["schema_name"]:
        _fail("FROM 스키마가 선택한 등록 원본과 다릅니다.")
    alias_node = table.args.get("alias")
    if alias_node:
        if any(value for key, value in alias_node.args.items() if key != "this"):
            _fail("테이블 별칭에서 컬럼 이름을 변경할 수 없습니다.")
        return _identifier(alias_node.this)
    return None


def _normalize(expression, source, alias):
    result = expression.copy()
    for column in result.find_all(exp.Column):
        if column.args.get("catalog"):
            _fail("외부 카탈로그 컬럼은 사용할 수 없습니다.")
        table = _identifier(column.args["table"]) if column.args.get("table") else None
        schema = _identifier(column.args["db"]) if column.args.get("db") else None
        if schema:
            if alias or table != source["table_name"] or schema != source["schema_name"]:
                _fail("컬럼의 스키마·테이블 한정자가 선택한 원본과 다릅니다.")
        elif table and table != (alias or source["table_name"]):
            _fail("등록되지 않은 테이블 또는 별칭의 컬럼입니다.")
        if not isinstance(column.this, exp.Star):
            column.set("this", exp.to_identifier(_identifier(column.this), quoted=True))
        for part in ("table", "db", "catalog"):
            column.set(part, None)
    return result


def _compile_scalar(expression, names, prefix):
    # compile_expression replaces Literal children; a literal at the root needs a
    # parent so it, too, is emitted exclusively as a bound parameter.
    if isinstance(expression, exp.Literal):
        expression = exp.Paren(this=expression.copy())
    try:
        return compile_expression(expression.sql(dialect="postgres"), names, prefix)
    except RecursionError:
        _fail("SQL 중첩이 너무 깊습니다. 괄호나 계산식을 단순하게 작성하세요.")


def sql_to_form(sql: str, source: dict, available_relations=()) -> dict:
    """Parse accepted SQL into the same lossless definition edited by the form."""
    tree = _parse(sql)
    alias = _source_alias(tree, source)
    bindings = [(alias or source["table_name"], source, "")]
    selected, join_types = [], {}

    def normalize(expression):
        result = expression.copy()
        for column in result.find_all(exp.Column):
            if isinstance(column.this, exp.Star):
                continue
            name = _identifier(column.this)
            qualifier = _identifier(column.args["table"]) if column.args.get("table") else None
            schema = _identifier(column.args["db"]) if column.args.get("db") else None
            if column.args.get("catalog"):
                _fail("외부 카탈로그는 사용할 수 없습니다.")
            candidates = [(a, s, p) for a, s, p in bindings if name in {c["name"] for c in s["columns"]}
                          and (not qualifier or qualifier == a)
                          and (not schema or (schema == s["schema_name"] and a == s["table_name"]))]
            if len(candidates) != 1:
                _fail(f"등록되지 않았거나 모호한 컬럼입니다: {column.sql()}. 테이블 별칭을 명시하세요.")
            column.set("this", exp.to_identifier(candidates[0][2] + name, quoted=True))
            for part in ("table", "db", "catalog"):
                column.set(part, None)
        return result

    def conjuncts(node):
        if isinstance(node, exp.Paren):
            return conjuncts(node.this)
        return conjuncts(node.this) + conjuncts(node.expression) if isinstance(node, exp.And) else [node]

    for join in tree.args.get("joins", []):
        if len(selected) >= 8:
            _fail("JOIN은 최대 8개까지 지원합니다.")
        if not isinstance(join.this, exp.Table) or any(v for k, v in join.args.items() if k not in {"this", "on", "side", "kind"}):
            _fail("등록된 다대일 관계의 LEFT/INNER JOIN ... ON 조건만 지원합니다.")
        side, kind = join.args.get("side", "").upper(), join.args.get("kind", "").upper()
        if side not in {"", "LEFT"} or kind not in {"", "INNER", "OUTER"} or (kind == "OUTER" and side != "LEFT"):
            _fail("LEFT JOIN과 INNER JOIN만 지원합니다.")
        options = []
        for relation, target in available_relations:
            try:
                other_alias = _source_alias(exp.select("*").from_(join.this.copy()), target) or target["table_name"]
            except HTTPException:
                continue
            options.append((relation, target, other_alias))
        if not options or not join.args.get("on"):
            _fail("관리자가 등록한 관계의 테이블과 ON 조건을 사용하세요.")
        target, other_alias = options[0][1:]
        if any(a == other_alias or s["id"] == target["id"] for a, s, p in bindings):
            _fail("같은 테이블의 중복 JOIN 또는 중복 별칭은 지원하지 않습니다.")
        prefix = target["id"] + "__"
        bindings.append((other_alias, target, prefix))
        condition = normalize(join.args["on"])
        equalities = []
        for term in conjuncts(condition):
            if not isinstance(term, exp.EQ) or not isinstance(term.this, exp.Column) or not isinstance(term.expression, exp.Column):
                _fail("JOIN ON에는 등록된 키의 동등 조건만 사용할 수 있습니다.")
            equalities.append(frozenset((term.this.name, term.expression.name)))
        matched = []
        for relation, target, other_alias in options:
            key = frozenset((relation["left_column"], prefix + relation["right_column"]))
            allowed = {key}
            if source.get("project_column") and target.get("project_column"):
                allowed.add(frozenset((source["project_column"], prefix + target["project_column"])))
            if key in equalities and set(equalities).issubset(allowed):
                matched.append(relation)
        if len(matched) != 1:
            _fail("JOIN 조건이 등록된 다대일 관계와 일치하지 않습니다.")
        relation = matched[0]
        selected.append(relation["id"])
        join_types[relation["id"]] = "left" if side == "LEFT" else "inner"

    projected = []
    for original in tree.expressions:
        star = original if isinstance(original, exp.Star) else original.this if isinstance(original, exp.Column) and isinstance(original.this, exp.Star) else None
        if star is not None:
            if any(star.args.values()):
                _fail("SELECT *의 EXCEPT/REPLACE/RENAME은 지원하지 않습니다. 컬럼 목록을 명시하세요.")
            if isinstance(original, exp.Column):
                if original.args.get("catalog"):
                    _fail("외부 카탈로그는 사용할 수 없습니다.")
                qualifier = _identifier(original.args["table"]) if original.args.get("table") else None
                schema = _identifier(original.args["db"]) if original.args.get("db") else None
                tables = [(a,s,p) for a,s,p in bindings if a == qualifier and (not schema or (schema == s["schema_name"] and a == s["table_name"]))]
                if not tables:
                    _fail("등록되지 않은 테이블 별칭입니다.")
            else:
                tables = bindings
            # Joined star fields use source prefixes to keep output names unique.
            projected.extend((p+c["name"], exp.column(p+c["name"], quoted=True)) for a,s,p in tables for c in s["columns"])
            continue
        normalized = normalize(original)
        if isinstance(normalized, exp.Alias):
            projected.append((_identifier(normalized.args["alias"], output=True), normalized.this))
        elif isinstance(normalized, exp.Column):
            projected.append((_identifier(original.this), normalized))
        else:
            _fail("계산 컬럼에는 AS 새_컬럼명 별칭을 지정하세요.")
    if not projected or len(projected) > MAX_OUTPUT_COLUMNS:
        _fail("SQL 결과 컬럼은 1~100개여야 합니다.")
    output_names = set()
    for name, expression in projected:
        quote_output(name)
        if name.startswith("__"):
            _fail("출력 컬럼 이름은 __로 시작할 수 없습니다.")
        if name in output_names:
            _fail(f"출력 컬럼 이름이 중복됩니다: {name}. AS 별칭을 지정하세요.")
        output_names.add(name)
    group = tree.args.get("group")
    if group and any(v for k,v in group.args.items() if k != "expressions"):
        _fail("GROUPING SETS, ROLLUP, CUBE는 지원하지 않습니다.")
    groups = []
    source_names = {c["name"] for a,s,p in bindings for c in s["columns"]}
    outputs = dict(projected)
    for item in group.expressions if group else []:
        if isinstance(item, exp.Literal) and not item.is_string:
            try:
                position = int(item.this)
                if not 1 <= position <= len(projected):
                    raise ValueError()
            except ValueError:
                _fail("GROUP BY 출력 위치가 올바르지 않습니다.")
            normalized = projected[position-1][1]
        elif isinstance(item, exp.Column) and not item.table and item.name in outputs and item.name not in source_names:
            normalized = outputs[item.name]
        else:
            normalized = normalize(item)
        groups.append(normalized.sql(dialect="postgres"))
    return {"source_id":source["id"], "columns":[{"name":name,"expression":expr.sql(dialect="postgres")} for name,expr in projected],
            "filters":[], "joins":selected, "join_types":join_types,
            "where_sql":normalize(tree.args["where"].this).sql(dialect="postgres") if tree.args.get("where") else None,
            "group_by":groups,
            "having_sql":normalize(tree.args["having"].this).sql(dialect="postgres") if tree.args.get("having") else None,
            "raw_sql":None}


def compile_form(spec, source, relations=()):
    """Compile the unified definition. Only this representation reaches execution."""
    input_types = {c["name"]:c["type"] for c in source["columns"]}
    for relation, target in relations:
        for col in target["columns"]:
            name = target["id"] + "__" + col["name"]
            if name in input_types:
                _fail("관계 출력 컬럼 이름이 충돌합니다.")
            input_types[name] = col["type"]
    names = set(input_types)
    columns, expressions, params = [], {}, {}
    projections = [(c.name,c.expression) for c in spec.columns] or [(name,quote(name)) for name in input_types]
    grouped = bool(spec.group_by or spec.having_sql)
    trees = []
    for index, (name,expression) in enumerate(projections):
        fragment, bindings = compile_expression(expression, names, f"e{index}", aggregates=True)
        tree = sqlglot.parse_one(expression, read="postgres")
        trees.append(tree)
        grouped = grouped or bool(tree.find(exp.AggFunc))
        expressions[name] = fragment
        params.update(bindings)
        columns.append({"name":name,"type":input_types[tree.name] if isinstance(tree,exp.Column) else "derived"})
    where_sql, bindings = compile_expression(spec.where_sql or "TRUE", names, "rawWhere")
    params.update(bindings)
    groups = []
    for index, expression in enumerate(spec.group_by):
        fragment, bindings = compile_expression(expression, names, f"g{index}")
        params.update(bindings)
        groups.append(fragment)
    having, bindings = compile_expression(spec.having_sql or "TRUE", names, "having", aggregates=True)
    params.update(bindings)
    if grouped:
        def expression_key(tree):
            tree = tree.copy()
            for column in tree.find_all(exp.Column):
                column.set("this",exp.to_identifier(column.name,quoted=True))
            return tree.sql(dialect="postgres")
        group_keys = {expression_key(sqlglot.parse_one(g,read="postgres")) for g in spec.group_by}
        for tree in trees + [sqlglot.parse_one(spec.having_sql or "TRUE",read="postgres")]:
            reduced = tree.transform(lambda node: exp.Null() if isinstance(node,(exp.AggFunc,exp.Filter)) or expression_key(node) in group_keys else node)
            if reduced.find(exp.Column):
                _fail("집계하지 않은 출력·HAVING 컬럼은 GROUP BY에 포함해야 합니다.")
        # Identical constants must use identical PostgreSQL parameters in SELECT
        # and GROUP BY (including constants inside larger grouping expressions).
        canonical, replacements, unique = {}, {}, {}
        for key,value in params.items():
            identity = (type(value).__name__,str(value))
            if identity not in canonical:
                canonical[identity] = f"lit_{len(canonical)}"
                unique[canonical[identity]] = value
            replacements[key] = canonical[identity]
        def bind(fragment):
            return re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", lambda m: ":"+replacements.get(m[1],m[1]), fragment)
        expressions = {k:bind(v) for k,v in expressions.items()}
        groups, where_sql, having = [bind(g) for g in groups],bind(where_sql),bind(having)
        params = unique
    return {"columns":columns,"expressions":expressions,"params":params,"where_sql":where_sql,
            "group_sql":groups,"having_sql":having,"grouped":grouped}


def compile_raw_sql(sql: str, source: dict, available_relations=()) -> dict:
    from .schemas import DatasetSpec
    try:
        form = DatasetSpec.model_validate(sql_to_form(sql, source, available_relations))
    except (ValidationError, RecursionError):
        _fail("SQL 표현식 또는 그룹 기준이 허용 크기를 초과했습니다. 식을 단순하게 작성하세요.")
    relations = [(dict(r,join_type=form.join_types.get(r["id"],r["join_type"])),s) for r,s in available_relations if r["id"] in form.joins]
    result = compile_form(form,source,relations)
    result.update(spec=form,relations=relations)
    return result


def _value(value):
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, str):
        return exp.Literal.string(value)
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, (float, Decimal)) and not math.isfinite(value):
            _fail("필터의 숫자는 유한한 값이어야 합니다.")
        return exp.Literal.number(str(value))
    _fail("필터 값은 문자열·숫자·참/거짓·NULL이어야 합니다.")


def _form_expression(expression, names, aggregates=False):
    compile_expression(expression, names, aggregates=aggregates)
    tree = sqlglot.parse_one(expression, read="postgres")
    # The form compiler treats names as exact identifiers; preserve that behavior.
    for column in tree.find_all(exp.Column):
        column.set("this", exp.to_identifier(column.name, quoted=True))
    return tree


def form_to_sql(spec, source: dict, project_id: int | None = None, relations=()) -> str:
    """Produce one canonical SQL view for both editors, including registered joins."""
    if isinstance(spec, dict):
        from .schemas import DatasetSpec
        spec = DatasetSpec.model_validate(spec)
    if getattr(spec, "raw_sql", None) is not None:
        spec = compile_raw_sql(spec.raw_sql,source,relations)["spec"]
    by_id = {r["id"]:(r,s) for r,s in relations}
    if set(spec.joins) - set(by_id):
        _fail("등록된 관계 정보를 찾을 수 없습니다.")
    relations = [by_id[id] for id in spec.joins]
    compiled = compile_form(spec,source,relations)
    fields = {c["name"]:("s",c["name"]) for c in source["columns"]}
    for i,(relation,target) in enumerate(relations):
        fields.update({target["id"]+"__"+c["name"]:(f"j{i}",c["name"]) for c in target["columns"]})
    names = set(fields)

    def physical(expression):
        expression = expression.copy()
        for column in expression.find_all(exp.Column):
            alias, name = fields[column.name]
            column.set("this",exp.to_identifier(name,quoted=True))
            column.set("table",exp.to_identifier(alias,quoted=True))
        return expression

    if spec.columns:
        outputs = {column.name: _form_expression(column.expression, names, True) for column in spec.columns}
    else:
        outputs = {name:exp.column(name,quoted=True) for name in fields}
    projections = []
    for name, expression in outputs.items():
        projection = physical(expression)
        # A direct column already has its physical name in PostgreSQL. Keep
        # aliases for renamed/join-prefixed fields and computed expressions.
        if not isinstance(projection, exp.Column) or projection.name != name:
            projection = exp.alias_(projection, name, quoted=True)
        projections.append(projection)
    predicates = [_form_expression(spec.where_sql,names)] if spec.where_sql else []
    having = [_form_expression(spec.having_sql,names,True)] if spec.having_sql else []
    operators = {"eq": exp.EQ, "ne": exp.NEQ, "gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}
    for item in spec.filters:
        if item.column not in outputs:
            _fail(f"필터 컬럼이 없습니다: {item.column}")
        column = outputs[item.column].copy()
        if item.value is None and item.op in {"eq", "ne"}:
            predicate = exp.Is(this=column, expression=exp.Null())
            if item.op == "ne":
                predicate = exp.Not(this=predicate)
        elif item.op in {"in", "not_in"}:
            if not isinstance(item.value, list) or len(item.value) > 100:
                operator = "NOT IN" if item.op == "not_in" else "IN"
                _fail(f"{operator} 필터는 100개 이하의 값 목록이어야 합니다.")
            predicate = exp.In(this=column, expressions=[_value(value) for value in item.value]) if item.value else exp.Boolean(this=False)
            if item.op == "not_in":
                predicate = exp.Not(this=predicate) if item.value else exp.Boolean(this=True)
        elif item.op == "contains":
            if not isinstance(item.value, str) or len(item.value) > 2000:
                _fail("contains 필터는 2,000자 이하의 문자열이어야 합니다.")
            pattern = "%" + item.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            # PostgreSQL LIKE/ILIKE uses backslash as its default escape character.
            predicate = exp.ILike(this=exp.Cast(this=column, to=exp.DataType.build("TEXT")), expression=exp.Literal.string(pattern))
        else:
            if item.op not in operators:
                _fail("지원하지 않는 필터입니다.")
            predicate = operators[item.op](this=column, expression=_value(item.value))
        (having if compiled["grouped"] else predicates).append(predicate)
    if project_id is not None and source.get("project_column"):
        if not isinstance(project_id, int) or isinstance(project_id, bool):
            _fail("프로젝트 ID는 정수여야 합니다.")
        project_predicate = exp.EQ(this=exp.column(source["project_column"], quoted=True), expression=_value(project_id))
        def terms(node):
            if isinstance(node,exp.Paren):
                return terms(node.this)
            return terms(node.this)+terms(node.expression) if isinstance(node,exp.And) else [node]
        if not any(t == project_predicate for p in predicates for t in terms(p)):
            predicates.append(project_predicate)
    def table(item,alias):
        return exp.Table(this=exp.to_identifier(item["table_name"],quoted=True),db=exp.to_identifier(item["schema_name"],quoted=True)).as_(alias,quoted=True)
    query = exp.select(*projections).from_(table(source,"s"))
    for i,(relation,target) in enumerate(relations):
        on = [exp.EQ(this=exp.column(relation["left_column"],table="s",quoted=True),expression=exp.column(relation["right_column"],table=f"j{i}",quoted=True))]
        if source.get("project_column") and target.get("project_column"):
            on.append(exp.EQ(this=exp.column(source["project_column"],table="s",quoted=True),expression=exp.column(target["project_column"],table=f"j{i}",quoted=True)))
        query = query.join(table(target,f"j{i}"),on=exp.and_(*on),join_type=spec.join_types.get(relation["id"],relation["join_type"]).upper())
    if predicates:
        query = query.where(exp.and_(*(physical(p) for p in predicates)))
    if spec.group_by:
        query = query.group_by(*(physical(_form_expression(g,names)) for g in spec.group_by))
    if having:
        query = query.having(exp.and_(*(physical(p) for p in having)))
    sql = generated_sql(query)
    # A converted statement must pass exactly the same gate as manually entered SQL.
    compile_raw_sql(sql, source, relations)
    return sql
