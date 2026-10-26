from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models import uid, utcnow


class SemanticBase(DeclarativeBase):
    """Separate metadata allows the additive migration to hold its own lock."""


class KnowledgeItem(SemanticBase):
    __tablename__ = "app_semantic_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    connection_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    head_version: Mapped[int] = mapped_column(Integer, default=1)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeVersion(SemanticBase):
    __tablename__ = "app_semantic_versions"
    item_id: Mapped[str] = mapped_column(ForeignKey("app_semantic_items.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")


class BindingHead(SemanticBase):
    __tablename__ = "app_semantic_binding_heads"
    item_id: Mapped[str] = mapped_column(ForeignKey("app_semantic_items.id"), primary_key=True)
    definition_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    head_version: Mapped[int] = mapped_column(Integer, default=1)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class BindingVersion(SemanticBase):
    __tablename__ = "app_semantic_binding_versions"
    item_id: Mapped[str] = mapped_column(ForeignKey("app_semantic_items.id"), primary_key=True)
    definition_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    schema_snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")


class KnowledgeContext(SemanticBase):
    __tablename__ = "app_semantic_contexts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SemanticMigration(SemanticBase):
    __tablename__ = "app_semantic_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
