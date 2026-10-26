from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models import uid, utcnow


class AnalyticsBase(DeclarativeBase):
    pass


class EvaluationDatasetRecommendation(AnalyticsBase):
    __tablename__ = 'app_evaluation_dataset_recommendations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    question_ids: Mapped[list] = mapped_column(JSON, default=list)
    dataset_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisRun(AnalyticsBase):
    __tablename__ = "app_analysis_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), index=True)
    request: Mapped[dict] = mapped_column(JSON)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    source_watermark: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    question_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retry_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comparison_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AnalysisSchedule(AnalyticsBase):
    __tablename__ = 'app_analysis_schedules'
    __table_args__ = (UniqueConstraint('created_by', 'origin_run_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(36), index=True)
    origin_run_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request: Mapped[dict] = mapped_column(JSON)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    method_version: Mapped[str] = mapped_column(String(80))
    interval_hours: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    processed_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    outcome: Mapped[list] = mapped_column(JSON, default=list)
    outcome_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisNotification(AnalyticsBase):
    __tablename__ = 'app_analysis_notifications'
    __table_args__ = (UniqueConstraint('schedule_id', 'event_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    schedule_id: Mapped[str] = mapped_column(String(36), index=True)
    event_key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    comparison_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisProposal(AnalyticsBase):
    __tablename__ = "app_analysis_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_role: Mapped[str] = mapped_column(String(20))
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    workspace_token: Mapped[str] = mapped_column(String(200))
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    turn_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    request: Mapped[dict] = mapped_column(JSON)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    confirmed_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeWatch(AnalyticsBase):
    __tablename__ = 'app_knowledge_watches'
    __table_args__ = (UniqueConstraint('created_by', 'origin_run_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    connection_key: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(36))
    origin_run_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default='')
    last_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    processed_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def migrate_analytics(engine):
    if engine is None:
        return
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(7126002031)"))
        AnalyticsBase.metadata.create_all(connection)
        columns = {c['name'] for c in inspect(connection).get_columns('app_analysis_runs')}
        for name, definition in {
            'revision': 'INTEGER NOT NULL DEFAULT 1',
            'cancel_requested_at': 'TIMESTAMP WITH TIME ZONE', 'cancelled_at': 'TIMESTAMP WITH TIME ZONE',
            'retry_of': 'VARCHAR(36)', 'retry_run_id': 'VARCHAR(36)',
            'schedule_id': 'VARCHAR(36)', 'scheduled_for': 'TIMESTAMP WITH TIME ZONE',
            'comparison_run_id': 'VARCHAR(36)',
        }.items():
            if name not in columns:
                connection.execute(text(f'ALTER TABLE app_analysis_runs ADD COLUMN {name} {definition}'))
        for table, name in [('app_analysis_schedules', 'outcome_run_id'),
                            ('app_analysis_notifications', 'comparison_run_id')]:
            if name not in {c['name'] for c in inspect(connection).get_columns(table)}:
                connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} VARCHAR(36)'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_analysis_schedule_history ON app_analysis_runs (schedule_id, created_at, id)'))
