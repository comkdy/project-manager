"""A single exclusion policy for discovery, saved definitions and execution."""
from fastapi import HTTPException
from sqlalchemy import func, select

from .db import ignored_tables, source_database_key
from .direct_sql import parse_query, referenced_sources
from .models import DatasetVersion, Relation, Source


def table_ignored(schema, table):
    ignored = ignored_tables()
    return ([schema, table] in ignored or (schema, table) in ignored
            or table in ignored or f"{schema}.{table}" in ignored)


def source_visible(source):
    connection = getattr(source, "connection_key", None)
    return (connection in (None, source_database_key())
            and getattr(source, "available", True) is not False
            and not table_ignored(source.schema_name, source.table_name))


def require_source_visible(source):
    if not source_visible(source):
        raise HTTPException(404, "앱에서 제외된 원천 테이블입니다.")


class TableVisibility:
    def __init__(self, sources=(), relations=()):
        self.sources = {s.id: s for s in sources}
        self.relations = {r.id: r for r in relations}
        self.catalog = [{"id": s.id, "schema_name": s.schema_name, "table_name": s.table_name}
                        for s in sources if getattr(s, "connection_key", None) in (None, source_database_key())]
        self.hidden_sources = {s.id for s in sources if not source_visible(s)}
        self.hidden_relations = {r.id for r in relations
                                if r.left_source_id in self.hidden_sources or r.right_source_id in self.hidden_sources}

    @classmethod
    def load(cls, db, *, include_relations=True):
        return cls(list(db.scalars(select(Source))),
                   list(db.scalars(select(Relation))) if include_relations else [])

    def allows_spec(self, spec):
        if not self.hidden_sources:
            return True
        if spec.get("source_id") in self.hidden_sources or self.hidden_relations.intersection(spec.get("joins", [])):
            return False
        if spec.get("raw_sql"):
            try:
                sources = referenced_sources(parse_query(spec["raw_sql"]), self.catalog)
            except HTTPException:
                # A broken saved query must remain editable. The execution
                # validator still rejects invalid or unregistered references.
                return True
            return not any(s["id"] in self.hidden_sources for s in sources)
        return True

    def hidden_dataset_keys(self, db):
        if not self.hidden_sources:
            return set()
        return {(d.id, d.version) for d in db.scalars(select(DatasetVersion)) if not self.allows_spec(d.spec)}


def latest_dataset_versions(db, cards):
    ids = {c["dataset_id"] for c in cards}
    if not ids:
        return {}
    return dict(db.execute(select(DatasetVersion.id, func.max(DatasetVersion.version))
                           .where(DatasetVersion.id.in_(ids)).group_by(DatasetVersion.id)).all())


def hidden_card_ids(db, cards):
    policy = TableVisibility.load(db)
    hidden = policy.hidden_dataset_keys(db)
    gantt_hidden = {c['id'] for c in cards if c.get('kind') == 'gantt' and (c.get('gantt') or {}).get('data_mode') != 'dataset' and
                   any((c.get('gantt') or {}).get(role + '_source_id') in policy.hidden_sources for role in ('status', 'tat', 'history'))}
    if not hidden:
        return gantt_hidden
    latest = latest_dataset_versions(db, cards)
    return gantt_hidden | {c["id"] for c in cards if (c["dataset_id"], latest.get(c["dataset_id"], c["dataset_version"])) in hidden}
