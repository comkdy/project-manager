"""Authoritative contract for the read-only dataset change impact preview.

POST /api/datasets/{dataset_id}/impact accepts schemas.DatasetUpdate.
It shares the version/scope/visibility checks of saving a new version:
404 unavailable, 409 stale expected_version, 422 invalid scope/specification.
Compatibility is a static configuration check, not execution of card scripts.
"""

from typing import Literal

from .schemas import StrictModel


class DatasetColumnChange(StrictModel):
    name: str
    change: Literal['added', 'removed', 'type_changed']
    previous_type: str | None
    next_type: str | None


class DatasetCardImpact(StrictModel):
    dashboard_id: str
    dashboard_version: int
    project_id: int | None
    project_name: str | None
    card_id: str
    card_title: str
    kind: str
    status: Literal['compatible', 'review', 'incompatible']
    issues: list[str]


class DatasetImpact(StrictModel):
    dataset_id: str
    current_version: int
    proposed_version: int
    project_id: int | None
    validation_mode: Literal['static']
    column_changes: list[DatasetColumnChange]
    cards: list[DatasetCardImpact]
    notes: list[str]
