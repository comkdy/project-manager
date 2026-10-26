"""Reject a source connection that can mutate the database."""
from fastapi import HTTPException
from sqlalchemy import text


def require_read_only_role(connection):
    unsafe = connection.scalar(text("""
        SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
          OR has_database_privilege(current_user, current_database(), 'CREATE,TEMPORARY')
          OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace n
            WHERE has_schema_privilege(current_user, n.oid, 'CREATE')
          )
          OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            WHERE c.relkind IN ('r','p','v','m','f')
              -- PostgreSQL exposes session settings through this public view;
              -- it is not a source table and is blocked by the SQL catalog gate.
              AND c.oid <> 'pg_catalog.pg_settings'::regclass
              AND has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')
          )
          OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            WHERE c.relkind='S' AND has_sequence_privilege(current_user, c.oid, 'USAGE,UPDATE')
          )
        FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
    """))
    if unsafe is not False:
        raise HTTPException(503, "원천 DB 연결에는 쓰기·생성·관리 권한이 없는 조회 전용 계정이 필요합니다.")
