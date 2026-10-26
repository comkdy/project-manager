"""Resolve measurement identities from saved SQL, never from an output alias alone."""
import json
from functools import lru_cache

from sqlglot import exp, parse_one
from sqlglot.lineage import lineage


DOMAIN_GUIDANCE = ('Inline의 한 측정 항목은 operation+parameter 쌍입니다. 서로 다른 operation의 같은 '
    'parameter를 하나로 합치거나 MAIN만으로 중요도·의존성을 설명하지 마세요. '
    '일반적인 검토 흐름은 wafer_his의 operation별 recipe/eqp_id → Inline operation+parameter → EPM parameter입니다. '
    '이 흐름은 분석 가설이며 인과관계나 공정 간 실제 연결을 자동 확정하지 않습니다. '
    '장비 eqp_id는 설명 가능한 범주형 공정 조건이며 Lot/wafer/record 식별자와 구분합니다.')


def _equalities(expression, alias):
    """Only positive conjunctive literal equalities constrain an item."""
    if isinstance(expression, exp.Paren):
        return _equalities(expression.this, alias)
    if isinstance(expression, exp.And):
        left, right = _equalities(expression.this, alias), _equalities(expression.expression, alias)
        return {k: left.get(k, set()) | right.get(k, set()) for k in left.keys() | right.keys()}
    if isinstance(expression, exp.EQ):
        for col, value in ((expression.this, expression.expression), (expression.expression, expression.this)):
            if isinstance(col, exp.Column) and col.name in {'operation', 'parameter'} and col.table in {'', alias} and isinstance(value, exp.Literal):
                return {col.name: {value.this}}
    return {}


def _merge(left, right):
    return {key: left.get(key, set()) | right.get(key, set()) for key in left.keys() | right.keys()}


def _paths(node, constraints=None, field=None):
    constraints = constraints or {}
    if isinstance(node.expression, exp.Table):
        table = node.expression.name
        if table in {'inline_data', 'epm_data'} and field == 'value':
            yield table, constraints, field
        elif table == 'wafer_his' and field in {'recipe', 'eqp_id'}:
            yield table, constraints, field
        return
    for child in node.downstream:
        alias = child.expression.alias_or_name if isinstance(child.expression, exp.Table) else child.name.rsplit('.', 1)[0]
        where = node.source.args.get('where') if isinstance(node.source, exp.Select) else None
        # An outer operation filter must belong to the SAME joined source as
        # the value. A different join arm's operation cannot label this value.
        inherited = {}
        for key, values in constraints.items():
            for selection in node.source.selects:
                value = selection.this if isinstance(selection, exp.Alias) else selection
                if isinstance(value, exp.Column) and value.table in {'', alias} and (
                        value.is_star or selection.alias_or_name == key and value.name == key):
                    inherited[key] = values
                elif isinstance(value, exp.Star) and len(node.downstream) == 1:
                    inherited[key] = values
        restricted = _merge(inherited, _equalities(where.this, alias) if where else {})
        next_field = child.name.rsplit('.', 1)[-1] if child.name != '*' else field
        yield from _paths(child, restricted, next_field)


@lru_cache(maxsize=128)
def _context(serialized):
    dataset, extra_filters = json.loads(serialized)
    spec = dataset.get('spec') or {}
    sql = spec.get('raw_sql')
    if not sql:
        # Common single-source form definitions retain their source meaning.
        source = next((s for s in dataset.get('sources', []) if s.get('id') and s['id'] == spec.get('source_id')), None)
        if source is None or spec.get('joins'):
            return {'labels': {}, 'items': [], 'unresolved_columns': []}
        selections = [exp.alias_(parse_one(c['expression'], read='postgres'), c['name'], quoted=True) for c in spec.get('columns', [])]
        tree = exp.select(*(selections or [exp.Star()])).from_(exp.Table(
            this=exp.to_identifier(source.get('table_name') or source.get('name'), quoted=True),
            db=exp.to_identifier(source.get('schema_name', 'public'), quoted=True)))
        if spec.get('where_sql'):
            tree = tree.where(parse_one(spec['where_sql'], read='postgres'))
        sql = tree.sql(dialect='postgres')
        extra_filters = [*spec.get('filters', []), *extra_filters]
    try:
        tree = parse_one(sql, read='postgres')
        if extra_filters:
            outer = exp.select('*').from_(tree.subquery('_measurement_items'))
            for f in extra_filters:
                if f.get('op') == 'eq' and isinstance(f.get('value'), (str, int, float)):
                    outer = outer.where(exp.EQ(this=exp.column(f['column'], quoted=True), expression=exp.convert(f['value'])))
            tree = outer
        labels, items, unresolved = {}, [], []
        for column in dataset.get('columns', []):
            name = column['name']
            try:
                paths = list(_paths(lineage(name, tree, dialect='postgres', trim_selects=False)))
            except Exception:
                paths = []
            identities = []
            for table, constraints, field in paths:
                operation, parameter = constraints.get('operation', set()), constraints.get('parameter', set())
                family = {'inline_data': 'inline', 'epm_data': 'epm', 'wafer_his': 'history'}[table]
                if len(operation) != 1 and family != 'epm' or len(parameter) != 1 and family != 'history':
                    unresolved.append(name)
                    continue
                op, para = next(iter(operation), ''), next(iter(parameter), '')
                label = (f'Inline {op} / {para}' if family == 'inline' else f'EPM {para}' if family == 'epm'
                         else f'공정 {op} / {"Recipe" if field == "recipe" else "장비 eqp_id"}')
                identities.append({'column': name, 'family': family, 'operation': op, 'parameter': para, 'field': field, 'label': label})
            unique = {json.dumps(v, sort_keys=True): v for v in identities}
            if len(unique) == 1 and name not in unresolved:
                item = next(iter(unique.values()))
                labels[name] = item['label']
                items.append(item)
            elif len(unique) > 1:
                unresolved.append(name)
        return {'labels': labels, 'items': items, 'unresolved_columns': sorted(set(unresolved))}
    except Exception:
        return {'labels': {}, 'items': [], 'unresolved_columns': []}


def item_context(dataset, filters=()):
    filters = [f.model_dump(mode='json') if hasattr(f, 'model_dump') else f for f in filters]
    return _context(json.dumps([dataset, filters], ensure_ascii=False, sort_keys=True, default=str))


def context_for_run(request):
    return item_context(request['_evaluation_dataset'], request.get('_evaluation_scope', {}).get('filters', []))


def display_result(result, request):
    if not result:
        return result
    return {**result, 'item_labels': context_for_run(request)['labels']}


def validate_items(dataset, settings, filters=()):
    from fastapi import HTTPException
    context = item_context(dataset, filters)
    selected = {*settings.metrics, *settings.features, getattr(settings, 'group_column', None)}
    unknown = selected.intersection(context['unresolved_columns'])
    if unknown:
        raise HTTPException(422, '측정 항목을 확정할 수 없습니다: ' + ', '.join(sorted(unknown)) +
            '. Inline은 operation+parameter, EPM은 parameter를 함께 좁히거나 항목별로 나눠 주세요.')
    return context
