"""Project selection is an execution boundary, independent of authored SQL."""
import hashlib

from fastapi import HTTPException
from sqlglot import exp


def project_dataset_id(project_id, source_id):
    return 'project_' + hashlib.sha256(f'{project_id}:{source_id}'.encode()).hexdigest()[:48]


def source_predicate(source, project_id, project):
    if project_id is None:
        return None
    names = {c['name'] for c in source['columns']}
    if project is not None:
        if 'lot_cd' not in names or not project.lot_cds:
            return None
        return exp.In(this=exp.column('lot_cd', quoted=True),
                      expressions=[exp.Literal.string(code) for code in project.lot_cds])
    column = source.get('project_column')
    if column and column in names:
        return exp.EQ(this=exp.column(column, quoted=True), expression=exp.Literal.number(project_id))
    return None


def project_sources(compiled, project_id):
    from .project_settings import managed_project
    project = managed_project(project_id)
    sources = compiled.get('sources')
    if sources is None:
        sources = [compiled['source'], *(target for _, target in compiled['relations'])]
    predicates = [(source, source_predicate(source, project_id, project)) for source in sources]
    if project_id is not None and sources and not any(predicate is not None for _, predicate in predicates):
        raise HTTPException(422, '프로젝트 범위를 확인할 수 없는 공통 원천입니다. 전체 프로젝트에서 조회하거나 프로젝트 구분이 있는 원천과 연결해 주세요.')
    return [(source, predicate) for source, predicate in predicates if predicate is not None]


def scoped_sql(compiled, project_id):
    from .dashboard_period import filter_source_predicate
    sql = compiled['direct_sql']
    if project_id is None:
        return sql
    for source, predicate in project_sources(compiled, project_id):
        sql = filter_source_predicate(sql, source, predicate)
    return sql


def dataset_kind(dataset, catalog):
    """Only app-created, unchanged direct-table definitions count as DB originals."""
    from .direct_sql import parse_query
    from .project_settings import managed_project
    from .source_discovery import default_sql
    from .query import source_json
    spec = dataset.spec
    source = catalog.sources.get(spec['source_id'])
    if source is None:
        return 'derived'
    expected = source.id if dataset.project_id is None else project_dataset_id(dataset.project_id, source.id)
    if dataset.id != expected:
        return 'derived'
    if not spec.get('raw_sql'):
        return 'source' if not any(spec.get(key) for key in ('columns', 'filters', 'joins', 'where_sql', 'group_by', 'having_sql', 'pivot')) else 'derived'
    project = managed_project(dataset.project_id) if dataset.project_id is not None else None
    original = default_sql(source_json(source), dataset.project_id, lot_cds=project.lot_cds if project else None)
    try:
        def key(sql):
            tree = parse_query(sql)
            # Generated old versions quoted every lowercase identifier.
            for node in tree.find_all(exp.Identifier):
                if node.name.islower():
                    node.set('quoted', False)
            return tree.sql(dialect='postgres', normalize=True, comments=False)
        return 'source' if key(spec['raw_sql']) == key(original) else 'derived'
    except HTTPException:
        return 'derived'
