"""Canonical, bounded read contracts for schedule history and stored evidence."""
from datetime import datetime
from typing import Literal

from ..schemas import StrictModel
from .schemas import AnalysisRequest


class HistoryRun(StrictModel):
    id: str
    status: Literal['queued', 'running', 'cancelling', 'cancelled', 'completed', 'failed', 'needs_input']
    created_at: datetime
    completed_at: datetime | None
    scheduled_for: datetime | None
    duration_seconds: float | None
    retry_of: str | None
    retry_run_id: str | None
    comparison_run_id: str | None
    question_count: int
    is_template: bool
    queried_at: datetime | None


class ScheduleHistory(StrictModel):
    workspace_token: str
    schedule_id: str
    items: list[HistoryRun]
    next_cursor: str | None
    hidden_count: int


class SeriesEvidence(StrictModel):
    status: str | None
    samples_a: int | None
    mean_a: float | None
    samples_b: int | None
    mean_b: float | None
    delta: float | None
    min_samples: int | None
    absolute_delta: float | None


class SeriesComparison(StrictModel):
    family: str
    operation: str
    parameter: str
    previous: SeriesEvidence | None
    current: SeriesEvidence | None
    change: Literal['added', 'removed', 'state', 'direction', 'values', 'unchanged', 'not_comparable']
    notification_change: bool | None


class ScheduleComparison(StrictModel):
    workspace_token: str
    schedule_id: str
    current: HistoryRun
    previous: HistoryRun | None
    current_request: AnalysisRequest
    previous_request: AnalysisRequest | None
    comparable: bool
    reasons: list[str]
    total_series: int
    changed_count: int | None
    notification_change_count: int | None
    matched_count: int
    offset: int
    has_more: bool
    rows: list[SeriesComparison]
