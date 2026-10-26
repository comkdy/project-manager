from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..models import uid, utcnow
from .models import SemanticBase


class CollectionSettings(SemanticBase):
    __tablename__ = "app_semantic_collection_settings"
    connection_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    max_questions: Mapped[int] = mapped_column(Integer, default=5)
    # Legacy persisted selection, ignored: collection now discovers all datasets.
    dataset_ids: Mapped[list] = mapped_column(JSON, default=list)
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_digest: Mapped[str] = mapped_column(String(64), default="")
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(30), default="idle")
    last_message: Mapped[str] = mapped_column(String(500), default="")
    execution_revision: Mapped[int] = mapped_column(Integer, default=1)


class CollectionDatasetScan(SemanticBase):
    __tablename__ = "app_semantic_dataset_scans"
    connection_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeQuestion(SemanticBase):
    __tablename__ = "app_semantic_questions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(1000))
    reason: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20))
    topic: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(200), default="")
    targets: Mapped[list] = mapped_column(JSON, default=list)
    dataset: Mapped[dict] = mapped_column(JSON)
    context_source_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="open")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deferred_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeAnswer(SemanticBase):
    __tablename__ = "app_semantic_answers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    question_id: Mapped[str] = mapped_column(ForeignKey("app_semantic_questions.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    targets: Mapped[list] = mapped_column(JSON, default=list)
    user_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CollectedDraft(SemanticBase):
    __tablename__ = "app_semantic_collected_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    question_id: Mapped[str] = mapped_column(ForeignKey("app_semantic_questions.id"), index=True)
    answer_id: Mapped[str] = mapped_column(ForeignKey("app_semantic_answers.id"), unique=True)
    proposal: Mapped[dict] = mapped_column(JSON)
    original_proposal: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    followups: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    item_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
