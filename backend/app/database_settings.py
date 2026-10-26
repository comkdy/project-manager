"""Admin-managed PostgreSQL connections and per-database table visibility."""
from dataclasses import replace
import ipaddress
import re
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import select, text
from sqlalchemy.engine import URL, make_url

from . import db as database
from .auth import get_current_user, require_admin
from .credential_store import decrypt_url, encrypt_url
from .db import SessionLocal, SourceBinding, database_key, settings, use_source
from .models import Audit, DatabaseMount, DatabaseProfile, DatabaseProfileRemoval
from .query import clear_cache, source_connection
from .schemas import StrictModel
from .source_discovery import database_tables, refresh_sources
from .table_visibility import table_ignored

router = APIRouter(tags=["database-settings"])
_mount_lock = threading.RLock()
# Slow connection probes serialize configuration writes, not ordinary reads.
# The PostgreSQL advisory lock below provides the same guarantee across workers.
_settings_lock = threading.RLock()


class ConnectionInput(StrictModel):
    profile_id: str | None = Field(default=None, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr | None = Field(default=None, max_length=4096)
    sslmode: Literal["prefer", "require", "verify-full", "disable"] = "prefer"

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        value = value.strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?", value):
                raise ValueError("호스트 이름 또는 IP 주소를 입력하세요.")
        return value

    @field_validator("database", "username")
    @classmethod
    def valid_name(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("올바른 DB 이름과 사용자 이름을 입력하세요.")
        return value


class TableVisibilityInput(StrictModel):
    schema_name: str = Field(min_length=1, max_length=255)
    table_name: str = Field(min_length=1, max_length=255)
    visible: bool


def environment_profile():
    if not settings.source_database_url:
        return None
    url = make_url(database.sqlalchemy_url(settings.source_database_url))
    return DatabaseProfile(id=database_key(url), host=url.host or "", port=url.port or 5432,
                           database=url.database or "", username=url.username or "",
                           sslmode=url.query.get("sslmode", "prefer"),
                           ignored_tables=list(settings.table_ignore), encrypted_url="")


def find_profile(db, id):
    profile = db.get(DatabaseProfile, id)
    if not profile and not db.get(DatabaseProfileRemoval, id):
        env = environment_profile()
        profile = env if env and env.id == id else None
    if not profile:
        raise HTTPException(404, "DB 연결을 찾을 수 없습니다.")
    return profile


def profile_url(profile):
    if profile.encrypted_url:
        return decrypt_url(profile.encrypted_url)
    return make_url(database.sqlalchemy_url(settings.source_database_url))


def profile_json(profile):
    return {key: getattr(profile, key) for key in
            ("id", "host", "port", "database", "username", "sslmode")}


def connection_url(db, body):
    old = find_profile(db, body.profile_id) if body.profile_id else None
    password = body.password.get_secret_value() if body.password else ""
    same_target = old and all(getattr(old, key) == getattr(body, key)
                              for key in ("host", "port", "database", "username"))
    if not password:
        if not same_target:
            raise HTTPException(422, "새 DB 또는 사용자로 연결할 때는 비밀번호를 입력하세요.")
        # A stored credential is never silently forwarded to a different host.
        saved_url = profile_url(old)
        return saved_url.set(query={**saved_url.query, "sslmode": body.sslmode})
    if same_target:
        try:
            saved_url = profile_url(old)
            return saved_url.set(password=password, query={**saved_url.query, "sslmode": body.sslmode})
        except HTTPException:
            # A new password also repairs a profile whose encryption key was lost.
            pass
    return URL.create("postgresql+psycopg", host=body.host, port=body.port,
                      database=body.database, username=body.username, password=password,
                      query={"sslmode": body.sslmode})


def profile_from_url(url, ignored=()):
    return DatabaseProfile(id=database_key(url), host=url.host, port=url.port or 5432,
                           database=url.database, username=url.username,
                           sslmode=url.query.get("sslmode", "prefer"),
                           encrypted_url=encrypt_url(url), ignored_tables=list(ignored))


def binding_for(profile, revision=0):
    return SourceBinding(database.make_engine(profile_url(profile), read_only=True), profile.id,
                         profile.id, revision, ignored_tables=tuple(profile.ignored_tables))


def load_active_source():
    """Refresh persisted selection per request, including in another server worker."""
    with _mount_lock:
        with SessionLocal() as db:
            mount = db.get(DatabaseMount, 1)
            if not mount:
                database.active_source = None
                return database.current_source()
            old = database.active_source
            if old and old.revision == mount.revision and old.profile_id == mount.profile_id and not old.error:
                return old
            profile = db.get(DatabaseProfile, mount.profile_id)
            try:
                binding = binding_for(profile, mount.revision)
            except HTTPException as error:
                # Do not fall back to a different DB when saved credentials fail.
                binding = SourceBinding(None, profile.id, profile.id, mount.revision, error.detail,
                                        tuple(profile.ignored_tables))
            database.active_source = binding
            if old and old.engine and old.engine is not binding.engine:
                old.engine.dispose()
            return binding


def check_connection():
    with source_connection() as connection:
        connection.execute(text("SELECT 1"))


@router.get("/api/database-status")
def database_status(user=Depends(get_current_user)):
    binding = database.current_source()
    result = {"workspace_token": database.workspace_token(), "connection_key": binding.key,
              "profile_id": binding.profile_id or binding.key or None}
    try:
        check_connection()
        return dict(result, status="connected", database=binding.engine.url.database)
    except HTTPException as error:
        return dict(result, status="error" if binding.key else "not_configured", message=error.detail)


@router.get("/api/admin/databases")
def list_databases(user=Depends(require_admin)):
    with SessionLocal() as db:
        profiles = list(db.scalars(select(DatabaseProfile).order_by(DatabaseProfile.database)))
        env = environment_profile()
        if env and not any(p.id == env.id for p in profiles) and not db.get(DatabaseProfileRemoval, env.id):
            profiles.insert(0, env)
        return {"profiles": [profile_json(p) for p in profiles],
                "active_id": database.current_source().key or None,
                "workspace_token": database.workspace_token()}


@router.post("/api/admin/databases/test")
def test_database(body: ConnectionInput, user=Depends(require_admin)):
    with SessionLocal() as db:
        url = connection_url(db, body)
    engine = database.make_engine(url, read_only=True)
    try:
        with use_source(SourceBinding(engine, database_key(url), ignored_tables=())):
            tables = database_tables(include_ignored=True)
        return {"status": "connected", "table_count": len(tables)}
    finally:
        engine.dispose()


@router.post("/api/admin/databases/mount")
def mount_database(body: ConnectionInput, user=Depends(require_admin)):
    # Compatibility for existing clients that explicitly save and activate.
    return save_or_activate(user, body=body, activate=True)


@router.post("/api/admin/databases")
def save_database(body: ConnectionInput, user=Depends(require_admin)):
    return save_or_activate(user, body=body)


@router.post("/api/admin/databases/{profile_id}/activate")
def activate_database(profile_id: str, user=Depends(require_admin)):
    return save_or_activate(user, profile_id=profile_id, activate=True)


@router.delete("/api/admin/databases/{profile_id}")
def delete_database_registration(profile_id: str, user=Depends(require_admin)):
    # Use the same write lock as activation and re-read the persisted selection, not
    # the request's earlier source binding. Never connect to the source DB here.
    with _settings_lock:
        with SessionLocal.begin() as db:
            db.execute(text("SELECT pg_advisory_xact_lock(7126002004)"))
            profile = find_profile(db, profile_id)
            mount = db.get(DatabaseMount, 1)
            env = environment_profile()
            active_id = mount.profile_id if mount else (env.id if env else None)
            if active_id == profile_id:
                raise HTTPException(409, "활성 DB는 삭제할 수 없습니다. 다른 DB를 활성화한 뒤 등록을 삭제하세요.")
            db.merge(DatabaseProfileRemoval(id=profile_id, ignored_tables=list(profile.ignored_tables)))
            saved_profile = db.get(DatabaseProfile, profile_id)
            if saved_profile:
                db.delete(saved_profile)
            db.add(Audit(user_id=user.id, action="database.unregister", entity_id=profile_id,
                         details={"database": profile.database}))
    return {"profile_id": profile_id}


def save_or_activate(user, *, body=None, profile_id=None, activate=False):
    candidate = None
    mounted = False
    # A single persisted mount row makes activation exclusive across workers.
    with _settings_lock:
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT pg_advisory_xact_lock(7126002004)"))
                url = connection_url(db, body) if body else profile_url(find_profile(db, profile_id))
                key = database_key(url)
                old_profile = db.get(DatabaseProfile, key)
                removal = db.get(DatabaseProfileRemoval, key)
                env = environment_profile()
                ignored = old_profile.ignored_tables if old_profile else removal.ignored_tables if removal else (
                    env.ignored_tables if env and env.id == key else [])
                profile = profile_from_url(url, ignored)
                mount = db.get(DatabaseMount, 1)
                active_key = mount.profile_id if mount else (env.id if env else None)
                # Updating the active DB's credentials reapplies that same DB;
                # registering/editing any other profile never switches the app.
                activate = activate or active_key == key
                revision = (mount.revision if mount else 0) + 1
                candidate = binding_for(profile, revision)
                with use_source(candidate):
                    # Discovery, permission checks and metadata creation all finish
                    # before the active connection changes. Failure rolls back.
                    catalog = refresh_sources(db) if activate else database_tables(include_ignored=True)
                if env and not db.get(DatabaseProfile, env.id) and env.id != key and not db.get(DatabaseProfileRemoval, env.id):
                    db.add(profile_from_url(profile_url(env), env.ignored_tables))
                db.merge(profile)
                if removal:
                    db.delete(removal)
                db.flush()
                if activate:
                    if mount:
                        mount.profile_id, mount.revision = key, revision
                    else:
                        db.add(DatabaseMount(id=1, profile_id=key, revision=revision))
                db.add(Audit(user_id=user.id, action="database.mount" if activate else "database.save", entity_id=key,
                             details={"database": profile.database, "table_count": len(catalog)}))
                # Return the metadata connection before taking the reader lock;
                # a reader may itself be waiting for a pool connection.
                db.commit()
                # Only in-memory publication shares a lock with binding reads.
                with _mount_lock:
                    if activate:
                        previous = database.active_source
                        # Another worker may already have published a newer mount.
                        if previous is None or previous.revision <= candidate.revision:
                            mounted = True
                            database.active_source = candidate
                            if previous and previous.engine:
                                previous.engine.dispose()
                            clear_cache()
                return {"status": "connected", "profile_id": key, "active": activate, "table_count": len(catalog),
                        "workspace_token": f"{key}:{revision}" if activate else database.workspace_token()}
        finally:
            if candidate and not mounted:
                candidate.engine.dispose()


@router.get("/api/admin/databases/{profile_id}/tables")
def list_database_tables(profile_id: str, user=Depends(require_admin)):
    with SessionLocal() as db:
        profile = find_profile(db, profile_id)
        binding = binding_for(profile)
    try:
        with use_source(binding):
            tables = database_tables(include_ignored=True)
            return {"tables": [dict(t, visible=not table_ignored(t["schema_name"], t["table_name"])) for t in tables]}
    finally:
        binding.engine.dispose()


@router.put("/api/admin/databases/{profile_id}/table-visibility")
def set_table_visibility(profile_id: str, body: TableVisibilityInput, user=Depends(require_admin)):
    candidate = None
    activated = False
    with _settings_lock:
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT pg_advisory_xact_lock(7126002004)"))
                profile = find_profile(db, profile_id)
                candidate = binding_for(profile)
                with use_source(candidate):
                    tables = database_tables(include_ignored=True)
                if not any(t["schema_name"] == body.schema_name and t["table_name"] == body.table_name for t in tables):
                    raise HTTPException(404, "조회 가능한 테이블을 찾을 수 없습니다.")
                # Expand legacy bare names once so a switch always affects exactly
                # one table, even if other schemas contain the same table name.
                pairs, unmatched = set(), []
                for entry in profile.ignored_tables:
                    if isinstance(entry, list):
                        pairs.add(tuple(entry))
                    elif "." in entry:
                        pairs.add(tuple(entry.split(".", 1)))
                    else:
                        matches = [(t["schema_name"], t["table_name"]) for t in tables if t["table_name"] == entry]
                        pairs.update(matches)
                        if not matches:
                            unmatched.append(entry)
                pair = (body.schema_name, body.table_name)
                pairs.discard(pair) if body.visible else pairs.add(pair)
                # Store schema and table separately: quoted names can contain dots.
                ignored = [list(p) for p in sorted(pairs)] + unmatched
                name = f"{body.schema_name}.{body.table_name}"
                if not profile.encrypted_url:
                    profile = profile_from_url(profile_url(profile), ignored)
                    db.add(profile)
                else:
                    profile.ignored_tables = ignored
                candidate = replace(candidate, ignored_tables=tuple(ignored))
                with use_source(candidate):
                    refresh_sources(db)
                mount = db.get(DatabaseMount, 1)
                active = mount.profile_id == profile_id if mount else database.current_source().key == profile_id
                if active:
                    revision = (mount.revision if mount else 0) + 1
                    if mount:
                        mount.revision = revision
                    else:
                        db.add(DatabaseMount(id=1, profile_id=profile_id, revision=revision))
                    candidate = replace(candidate, revision=revision)
                db.add(Audit(user_id=user.id, action="database.table_visibility", entity_id=profile_id,
                             details={"table": name, "visible": body.visible}))
                db.commit()
                with _mount_lock:
                    if active:
                        previous = database.active_source
                        if previous is None or previous.revision <= candidate.revision:
                            database.active_source = candidate
                            activated = True
                            if previous and previous.engine:
                                previous.engine.dispose()
                            clear_cache()
                return {"visible": body.visible, "active": active,
                        "workspace_token": f"{profile_id}:{candidate.revision}" if active else database.workspace_token()}
        finally:
            if candidate and not activated:
                candidate.engine.dispose()
