"""Server-owned analysis review contract, shared with the UI type generator."""
from datetime import datetime
from typing import Literal

from pydantic import Field

from ..semantic.schemas import Strict
from .schemas import AnalysisRequest


class ProposalProjectScope(Strict):
    id: int
    name: str
    lot_cds: list[str]


class ProposalDisplay(Strict):
    family: Literal["inline", "epm"] | None = None
    parameter: str | None = None


class AnalysisProposalView(Strict):
    kind: Literal["analysis_proposal"] = "analysis_proposal"
    proposal_id: str
    revision: int | None = None
    status: Literal["pending", "confirmed", "cancelled", "unavailable"]
    workspace_token: str | None = None
    project_id: int | None = None
    project_scope: ProposalProjectScope | None = None
    request: AnalysisRequest | None = None
    goal: Literal["describe", "cause", "outlier"] = "describe"
    display: ProposalDisplay = Field(default_factory=ProposalDisplay)
    missing: list[str] = Field(default_factory=list)
    criteria_missing: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)
    explanation: str
    created_at: datetime | None = None
    run_id: str | None = None


class AnalysisProposalConfirm(Strict):
    expected_revision: int = Field(ge=1)
    workspace_token: str = Field(min_length=1, max_length=200)
    project_id: int | None = None
    request: AnalysisRequest
