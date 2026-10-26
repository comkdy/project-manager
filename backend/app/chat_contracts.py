"""Typed boundaries between skills, tools and the chat UI."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from .schemas import CardSpec, DatasetSpec, ISOWeek, PivotSpec, StrictModel
from .analytics.chat_contracts import AnalysisCall
from .evaluation_narrative import Narrative
from .chat_data_contracts import DataAnalysisCall, DataAnalysisPlan


class Clarification(StrictModel):
    kind: Literal["clarification"]
    question: str = Field(min_length=1, max_length=2000)
    options: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=4)


class Route(StrictModel):
    kind: Literal["route"]
    intent: Literal["query", "dataset", "card"]


class EvaluationCasesCall(StrictModel):
    kind: Literal["evaluation_cases"]


class QueryDraft(StrictModel):
    kind: Literal["query"]
    explanation: str = Field(min_length=1, max_length=3000)
    name: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=80)
    sql: str | None = Field(default=None, min_length=1, max_length=20000)
    pivot: PivotSpec | None = None

    @model_validator(mode="after")
    def one_query_definition(self):
        if (self.sql is None) == (self.pivot is None):
            raise ValueError("SQL 또는 단일 테이블 피벗 설정 중 하나만 작성하세요. JOIN은 SQL로 작성하세요.")
        return self


class DatasetDraft(StrictModel):
    kind: Literal["dataset"]
    explanation: str = Field(min_length=1, max_length=3000)
    name: str = Field(min_length=1, max_length=200)


class NeedsDataset(StrictModel):
    kind: Literal["needs_dataset"]
    query_request: str = Field(min_length=1, max_length=3000)
    reviewed_dataset_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=1000)
    missing_capability: str = Field(default='', max_length=1500)


class CardDraft(StrictModel):
    kind: Literal["card"]
    explanation: str = Field(min_length=1, max_length=3000)
    card: CardSpec


OUTPUTS = {
    "data_analysis_plan": TypeAdapter(DataAnalysisPlan),
    "evaluation_explanation": TypeAdapter(Narrative),
    "route": TypeAdapter(Annotated[Route | Clarification | AnalysisCall | EvaluationCasesCall | DataAnalysisCall, Field(discriminator="kind")]),
    "query": TypeAdapter(Annotated[QueryDraft | Clarification, Field(discriminator="kind")]),
    "dataset": TypeAdapter(Annotated[DatasetDraft | Clarification, Field(discriminator="kind")]),
    "card": TypeAdapter(Annotated[CardDraft | NeedsDataset | Clarification, Field(discriminator="kind")]),
}


class DatasetDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    spec: DatasetSpec


class ProposalDefinition(StrictModel):
    kind: Literal["query", "dataset", "card"]
    explanation: str = Field(default="", max_length=3000)
    dataset: DatasetDefinition | None = None
    card: CardSpec | None = None
    knowledge_context_id: str | None = Field(default=None, min_length=1, max_length=36)
    evaluation_dataset_recommendation_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode="after")
    def required_artifacts(self):
        if self.evaluation_dataset_recommendation_id and self.kind != 'dataset':
            raise ValueError('평가 사례의 생성안은 데이터셋으로 검토하세요.')
        if self.kind == "card" and self.card is None:
            raise ValueError("카드 설정이 필요합니다.")
        if self.kind != "card" and (self.dataset is None or self.card is not None):
            raise ValueError("쿼리·데이터셋 제안에는 데이터셋 정의만 필요합니다.")
        if self.dataset and (not self.dataset.name.strip() or self.dataset.spec.raw_sql is None):
            raise ValueError("데이터셋 이름과 SQL 원문이 필요합니다.")
        return self


class ProposalPreview(StrictModel):
    proposal: ProposalDefinition
    project_id: int | None = None
    period_start: ISOWeek | None = None
    workspace_token: str = Field(min_length=1, max_length=120)


class ProposalApply(ProposalPreview):
    request_id: UUID
    dashboard_id: str | None = Field(default=None, max_length=80)
    dashboard_version: int | None = Field(default=None, ge=1)
