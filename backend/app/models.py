import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Sequence
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, source_database_key


def uid():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "app_users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    # Retained only to preserve legacy data; password authentication is disabled.
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(20))
    employee_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    team: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_source: Mapped[str] = mapped_column(String(20), default="legacy")
    profile_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LlmCredential(Base):
    __tablename__ = "app_llm_credentials"
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True)
    encrypted_key: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(String(36), default=uid)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_config: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_status: Mapped[str] = mapped_column(String(20), default="unchecked")
    check_message: Mapped[str] = mapped_column(String(500), default="")


class LoginSession(Base):
    __tablename__ = "app_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Source(Base):
    __tablename__ = "app_sources"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    schema_name: Mapped[str] = mapped_column(String(80))
    table_name: Mapped[str] = mapped_column(String(80))
    project_column: Mapped[str | None] = mapped_column(String(80), nullable=True)
    primary_key: Mapped[str] = mapped_column(String(80))
    columns: Mapped[list] = mapped_column(JSON)
    connection_key: Mapped[str] = mapped_column(String(64), default=source_database_key)
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class Relation(Base):
    __tablename__ = "app_relations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    left_source_id: Mapped[str] = mapped_column(ForeignKey("app_sources.id"))
    right_source_id: Mapped[str] = mapped_column(ForeignKey("app_sources.id"))
    left_column: Mapped[str] = mapped_column(String(80))
    right_column: Mapped[str] = mapped_column(String(80))
    join_type: Mapped[str] = mapped_column(String(10), default="left")


class DatasetVersion(Base):
    __tablename__ = "app_dataset_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # NULL definitions are shared; a project definition always keeps this owner
    # across versions. The physical source table remains common/read-only.
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    spec: Mapped[dict] = mapped_column(JSON)
    columns: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Dashboard(Base):
    __tablename__ = "app_dashboards"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    cards: Mapped[list] = mapped_column(JSON, default=list)
    layout: Mapped[list] = mapped_column(JSON, default=list)
    connection_key: Mapped[str] = mapped_column(String(64), default=source_database_key)


class Audit(Base):
    __tablename__ = "app_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(120))
    entity_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatabaseProfile(Base):
    __tablename__ = "app_database_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    database: Mapped[str] = mapped_column(String(200))
    username: Mapped[str] = mapped_column(String(200))
    sslmode: Mapped[str] = mapped_column(String(20), default="prefer")
    ignored_tables: Mapped[list] = mapped_column(JSON, default=list)
    # Encrypt the complete URL so passwords and optional driver secrets stay private.
    encrypted_url: Mapped[str] = mapped_column(Text)


class DatabaseMount(Base):
    __tablename__ = "app_database_mount"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("app_database_profiles.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class DatabaseProfileRemoval(Base):
    """Remember unregistering an env-provided DB without keeping its credentials."""
    __tablename__ = "app_database_profile_removals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ignored_tables: Mapped[list] = mapped_column(JSON, default=list)


class AppProject(Base):
    __tablename__ = "app_projects"
    __table_args__ = (UniqueConstraint('connection_key', 'name'),)
    id: Mapped[int] = mapped_column(Integer, Sequence('app_project_id_seq', start=1000000), primary_key=True)
    connection_key: Mapped[str] = mapped_column(String(64), default=source_database_key, index=True)
    name: Mapped[str] = mapped_column(String(200))
    lot_cds: Mapped[list] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text, default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ProjectRemoval(Base):
    """Tombstone keeps a removed project ID from becoming an unscoped legacy ID."""
    __tablename__ = 'app_project_removals'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_key: Mapped[str] = mapped_column(String(64))
