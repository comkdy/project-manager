"""Read-only workspace activity, based solely on stored metadata timestamps."""
from datetime import datetime
from typing import Literal

from pydantic import Field

from .schemas import StrictModel


class ActivityTarget(StrictModel):
    kind: Literal['analysis_run', 'analysis_proposal', 'knowledge_collection']
    id: str | None = None
    project_id: int | None = None


class ActivityFreshness(StrictModel):
    state: Literal['recorded', 'unknown'] = 'unknown'
    queried_at: datetime | None = Field(default=None, description='Stored source transaction timestamp of this analysis; not the activity-list refresh time.')
    last_measured_at: datetime | None = Field(default=None, description='Latest measurement within the rows read by this analysis; not a whole-source freshness guarantee.')


class ActivityControls(StrictModel):
    revision: int = Field(ge=1)
    can_cancel: bool = False
    can_retry: bool = False
    retry_run_id: str | None = None
    retry_after: datetime | None = None


class ActivityActionRequest(StrictModel):
    target: ActivityTarget
    action: Literal['cancel', 'retry']
    expected_revision: int = Field(ge=1)
    workspace_token: str = Field(min_length=1, max_length=200)


class ActivityActionResult(StrictModel):
    target: ActivityTarget
    status: Literal['queued', 'running', 'cancelling', 'cancelled', 'needs_input', 'completed', 'failed']
    message: str


class WorkspaceActivityItem(StrictModel):
    id: str
    kind: Literal['analysis_run', 'analysis_proposal', 'knowledge_collection']
    title: str
    status: Literal['queued', 'running', 'cancelling', 'cancelled', 'needs_input', 'needs_confirmation', 'completed', 'failed', 'unknown']
    project_id: int | None = None
    project_name: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    activity_at: datetime | None = Field(default=None, description='Latest available stored execution timestamp; unknown events are not estimated.')
    freshness: ActivityFreshness = Field(default_factory=ActivityFreshness)
    question_count: int = Field(default=0, ge=0)
    message: str = ''
    target: ActivityTarget
    controls: ActivityControls | None = None


class WorkspaceActivity(StrictModel):
    workspace_token: str
    project_id: int | None = None
    generated_at: datetime = Field(description='Time this metadata summary was prepared; never evidence of a fresh measurement query.')
    timestamp_basis: Literal['stored_execution_records'] = 'stored_execution_records'
    limit: int = Field(default=60, ge=1, le=100)
    has_more: bool = False
    items: list[WorkspaceActivityItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
