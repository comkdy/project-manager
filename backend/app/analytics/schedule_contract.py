"""Canonical contract for confirmed, rolling analysis schedules and app notices."""
from datetime import datetime
from typing import Literal

from pydantic import Field

from ..schemas import StrictModel
from .schemas import AnalysisRequest


class SchedulePreviewRequest(StrictModel):
    source_run_id: str = Field(min_length=1, max_length=36)
    interval_hours: Literal[1, 6, 24, 168] = 24
    workspace_token: str = Field(min_length=1, max_length=200)


class ScheduleSave(SchedulePreviewRequest):
    name: str = Field(min_length=1, max_length=120)
    expected_run_revision: int = Field(ge=1)
    first_run_at: datetime


class SchedulePreview(StrictModel):
    interval_hours: Literal[1, 6, 24, 168]
    source_run_id: str
    source_run_revision: int
    workspace_token: str
    first_run_at: datetime
    request: AnalysisRequest
    notes: list[str]


class ScheduleAction(StrictModel):
    action: Literal['pause', 'resume', 'replace']
    expected_revision: int = Field(ge=1)
    workspace_token: str = Field(min_length=1, max_length=200)
    replacement: ScheduleSave | None = None


class ScheduleView(StrictModel):
    id: str
    name: str
    project_id: int | None
    origin_run_id: str
    kind: Literal['statistics', 'trend', 'split']
    interval_hours: int
    enabled: bool
    revision: int
    next_run_at: datetime | None
    last_run_id: str | None
    message: str
    can_manage: bool
    request: AnalysisRequest


class AnalysisNotice(StrictModel):
    id: str
    schedule_id: str
    schedule_name: str
    project_id: int | None
    kind: Literal['changed', 'failed', 'needs_input', 'cancelled', 'blocked']
    message: str
    run_id: str | None
    comparison_run_id: str | None
    created_at: datetime
    acknowledged_at: datetime | None


class ScheduleList(StrictModel):
    workspace_token: str
    items: list[ScheduleView]
    notifications: list[AnalysisNotice]
    unread_count: int
    has_more_notifications: bool


class NoticeAck(StrictModel):
    workspace_token: str = Field(min_length=1, max_length=200)
