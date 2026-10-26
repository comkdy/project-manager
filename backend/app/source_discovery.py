"""Discover the connected database without changing its tables or data."""
import hashlib
import json

from sqlalchemy import select, text
from sqlglot import exp

from .db import source_database_key
from .models import DatasetVersion, Source
from .query import source_connection, source_json
from .schemas import DatasetSpec
from .table_visibility import table_ignored
from .sql_display import generated_sql
from .dataset_versioning import lock_dataset_version
from .dataset_scope import project_dataset_id, source_predicate


def database_tables(*, include_ignored=False):
    with source_connection() as connection:
        rows = connection.execute(text("""
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   a.attname AS column_name, format_type(a.atttypid, NULL) AS column_type,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_index i
                       WHERE i.indrelid=c.oid AND i.indisprimary AND i.indnkeyatts=1
                         AND a.attnum=i.indkey[0]
                   ) AS single_primary_key
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
            WHERE c.relkind IN ('r','p','v','m','f')
              -- A partitioned parent is one logical source. Listing its child
              -- partitions again creates duplicate datasets for the same rows.
              AND NOT c.relispartition
              AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
              AND n.nspname <> 'information_schema'
              AND a.attnum > 0 AND NOT a.attisdropped
              AND has_schema_privilege(n.oid, 'USAGE')
              AND has_table_privilege(c.oid, 'SELECT')
            ORDER BY n.nspname, c.relname, a.attnum
        """)).mappings()
        tables = {}
        for row in rows:
            schema, table = row["schema_name"], row["table_name"]
            if not include_ignored and table_ignored(schema, table):
                continue
            item = tables.setdefault((schema, table), {
                "schema_name": schema, "table_name": table, "columns": [], "primary_key": "",
            })
            item["columns"].append({"name": row["column_name"], "type": row["column_type"]})
            if row["single_primary_key"]:
                item["primary_key"] = row["column_name"]
        return list(tables.values())


def source_id(schema, table):
    identity = [source_database_key(), schema, table]
    return "auto_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:48]


def default_sql(source, project_id=None, *, quote_all=False, lot_cds=None):
    # Build identifiers as syntax nodes; database names are never SQL fragments.
    query = exp.select(*[exp.column(c["name"], quoted=True) for c in source["columns"][:100]]).from_(
        exp.Table(this=exp.to_identifier(source["table_name"], quoted=True),
                  db=exp.to_identifier(source["schema_name"], quoted=True)))
    if lot_cds is not None and 'lot_cd' in {c['name'] for c in source['columns']}:
        from .project_settings import lot_predicate
        query = query.where(lot_predicate(lot_cds))
    elif project_id is not None and source["project_column"]:
        query = query.where(exp.EQ(this=exp.column(source["project_column"], quoted=True),
                                  expression=exp.Literal.number(project_id)))
    return generated_sql(query, quote_all=quote_all)


def refresh_sources(db, project_id=None, *, project=None, previous_codes=None):
    from .project_settings import managed_project
    project = project or managed_project(project_id, db=db)
    discovered = database_tables()
    # Only app metadata is written; the source connection is always read-only.
    db.execute(text("SELECT pg_advisory_xact_lock(7126001903)"))
    existing = {}
    live_tables = {(t["schema_name"], t["table_name"]) for t in discovered}
    for source in db.scalars(select(Source).where(Source.connection_key == source_database_key()).order_by(Source.id)):
        if not table_ignored(source.schema_name, source.table_name):
            source.available = (source.schema_name, source.table_name) in live_tables
        existing.setdefault((source.schema_name, source.table_name), []).append(source)
    result = []
    for table in discovered:
        schema, name = table["schema_name"], table["table_name"]
        if table_ignored(schema, name):
            continue
        automatic_id = source_id(schema, name)
        candidates = existing.get((schema, name), [])
        # Keep manually selected columns and project mappings when already configured.
        source = next((s for s in candidates if s.id != automatic_id), None)
        source = source or next(iter(candidates), None)
        # Recognize only our exact generated defaults, including the older
        # all-quoted format. Authored SQL and historical versions stay intact.
        previous_sqls = {default_sql(source_json(source), quote_all=quoted) for quoted in (False, True)} if source and source.columns else set()
        if source is None:
            source = Source(id=automatic_id, name=name, project_column=None,
                            connection_key=source_database_key(), available=True, **table)
            db.add(source)
        elif source.id == automatic_id:
            source.columns, source.primary_key = table["columns"], table["primary_key"]
        else:
            live = {c["name"]: c for c in table["columns"]}
            source.columns = [live[c["name"]] for c in source.columns if c["name"] in live]
            if source.primary_key not in live:
                source.primary_key = ""
            if source.project_column not in live:
                source.project_column = None
        if source.columns:
            item = source_json(source)
            result.append(dict(item, default_sql=default_sql(item, project_id, lot_cds=project.lot_cds if project else None)))
            if source.id == automatic_id:
                sql = default_sql(item)
                lock_dataset_version(db, source.id)
                latest = db.scalar(select(DatasetVersion).where(DatasetVersion.id == source.id)
                                   .order_by(DatasetVersion.version.desc()).limit(1))
                if latest is None or (latest.name == name and latest.spec.get("raw_sql") in previous_sqls
                                      and (latest.spec["raw_sql"] != sql or latest.columns != source.columns[:100])):
                    db.add(DatasetVersion(id=source.id, version=latest.version + 1 if latest else 1,
                                          name=name, columns=source.columns[:100],
                                          spec=DatasetSpec(source_id=source.id, raw_sql=sql).model_dump()))
            if project_id is not None and source_predicate(item, project_id, project) is not None:
                dataset_id = project_dataset_id(project_id, source.id)
                lock_dataset_version(db, dataset_id)
                latest = db.scalar(select(DatasetVersion).where(DatasetVersion.id == dataset_id)
                                   .order_by(DatasetVersion.version.desc()).limit(1))
                sql = default_sql(item, project_id, lot_cds=project.lot_cds if project else None)
                old_sql = default_sql(dict(item, columns=latest.columns if latest else item['columns']),
                                      project_id, lot_cds=previous_codes or (project.lot_cds if project else None))
                if latest is None or (latest.name == name and latest.spec.get('raw_sql') == old_sql
                                      and (old_sql != sql or latest.columns != source.columns[:100])):
                    db.add(DatasetVersion(id=dataset_id, version=latest.version + 1 if latest else 1,
                                          name=name, project_id=project_id, columns=source.columns[:100],
                                          spec=DatasetSpec(source_id=source.id, raw_sql=sql).model_dump()))
    db.flush()
    return result
