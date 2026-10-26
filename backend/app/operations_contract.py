"""Read-only operational diagnostics shared with the browser."""
from datetime import datetime
from typing import Literal
from .schemas import StrictModel

class OperationCheck(StrictModel):
    id: str
    title: str
    scope: str
    status: Literal['ok', 'attention', 'unknown', 'info']
    message: str
    guidance: str
    observed_at: datetime | None = None
    action: Literal['database', 'activity', 'knowledge', 'schedules', 'recovery', 'llm_settings'] | None = None

class OperationsStatus(StrictModel):
    workspace_token: str
    checked_at: datetime
    checks: list[OperationCheck]
    notes: list[str]
