"""Administrator recovery status without secrets or database contents."""
from datetime import datetime
from typing import Literal
from pydantic import Field
from .schemas import StrictModel

class RecoveryRequest(StrictModel):
    workspace_token: str = Field(min_length=1, max_length=200)
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')

class RecoveryCheck(StrictModel):
    label: str
    status: Literal['passed', 'attention']
    message: str

class BackupView(StrictModel):
    id: str
    status: Literal['running', 'verified', 'attention', 'failed', 'interrupted']
    stage: str
    created_at: datetime
    checked_at: datetime | None
    size_bytes: int
    table_count: int
    row_count: int
    checks: list[RecoveryCheck]
    message: str
    can_verify: bool
    folder: str

class RecoveryStatus(StrictModel):
    workspace_token: str
    supported: bool
    reason: str
    busy: bool
    backup_count: int
    can_create: bool
    items: list[BackupView]
    notes: list[str]
