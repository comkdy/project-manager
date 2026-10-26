from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8", extra="ignore",
    )
    app_database_url: str = ""
    source_database_url: str = ""
    db_credentials_key: str = ""
    table_ignore: list[str] = Field(default_factory=list)
    llm_base_url: str = ""
    llm_model: str = ""
    llm_credentials_key: str = ""
    demo_mode: bool = False
    auth_mode: Literal["local_admin", "sso"] = "sso"
    sso_cookie_name: str = ""
    sso_bootstrap_admin_employee_id: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_editor_password: str = ""
    bootstrap_viewer_password: str = ""
    cookie_secure: bool = False
    session_hours: int = 12
    query_timeout_ms: int = 30000
    cache_ttl_seconds: int = 60
    max_scatter_points: int = 20000
    max_render_points: int = 10000
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"

    @field_validator("table_ignore")
    @classmethod
    def valid_table_ignore(cls, entries):
        normalized = []
        for entry in entries:
            entry = entry.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", entry):
                raise ValueError("TABLE_IGNORE에는 table 또는 schema.table 이름을 JSON 목록으로 지정하세요.")
            if entry not in normalized:
                normalized.append(entry)
        return normalized


settings = Settings()


def sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def database_key(url):
    import hashlib
    import json
    if not url:
        return ""
    url = make_url(url)
    # Password/account rotation must not orphan a database's saved definitions.
    identity = [url.host, url.port or 5432, url.database]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


@dataclass(frozen=True)
class SourceBinding:
    engine: object = field(repr=False)
    key: str
    profile_id: str | None = None
    revision: int = 0
    error: str | None = None
    ignored_tables: tuple | None = None


_source_context: ContextVar[SourceBinding | None] = ContextVar("source_database", default=None)
active_source: SourceBinding | None = None


def current_source():
    return _source_context.get() or active_source or SourceBinding(
        source_engine, database_key(settings.source_database_url))


def source_database_key():
    return current_source().key


def ignored_tables():
    ignored = current_source().ignored_tables
    return settings.table_ignore if ignored is None else ignored


def workspace_token():
    source = current_source()
    return f"{source.key}:{source.revision}"


@contextmanager
def use_source(binding):
    # The engine and its identity travel together for the whole request,
    # including work sent to the thread pool and streamed chat responses.
    token = _source_context.set(binding)
    try:
        yield binding
    finally:
        _source_context.reset(token)


def make_engine(url: str | URL, *, read_only=False):
    if not url:
        return None
    url = make_url(sqlalchemy_url(url) if isinstance(url, str) else url)
    if url.drivername not in {"postgresql", "postgresql+psycopg", "postgres"}:
        raise ValueError("Only PostgreSQL connections are supported")
    url = url.set(drivername="postgresql+psycopg")
    connect_args = {"connect_timeout": 5}
    if read_only:
        connect_args["options"] = "-c default_transaction_read_only=on"
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, hide_parameters=True,
                         connect_args=connect_args)


engine = make_engine(settings.app_database_url)
source_engine = make_engine(settings.source_database_url, read_only=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db
