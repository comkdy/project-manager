from typing import Literal

from pydantic import Field, model_validator

from .schemas import CreateItem, Kind, Strict, EvaluationClaim


Topic = Literal["grain", "column_meaning", "calculation", "time_basis", "exclusions", "deduplication", "relationship", "other"]


class QuestionTarget(Strict):
    kind: Literal["dataset", "column", "column_value"]
    column: str = Field(default="", max_length=200)
    source_id: str = Field(default="", max_length=80)
    value: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "dataset" and (self.column or self.source_id or self.value):
            raise ValueError("데이터셋 대상에는 컬럼·값을 지정하지 않습니다.")
        if self.kind != "dataset" and not self.column.strip():
            raise ValueError("질문 대상 컬럼을 지정하세요.")
        if (self.kind == "column_value") != bool(self.value):
            raise ValueError("컬럼 값 질문에는 특정 값을 지정하세요.")
        return self


class QuestionCandidate(Strict):
    dataset_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=2000)
    kind: Kind
    topic: Topic
    subject: str = Field(default="", max_length=200)
    targets: list[QuestionTarget] = Field(default_factory=list, max_length=8)


class GeneratedQuestions(Strict):
    questions: list[QuestionCandidate] = Field(default_factory=list, max_length=10)


class GroundedField(Strict):
    value: str = Field(min_length=1, max_length=6000)
    answer_id: str = Field(min_length=1, max_length=36)
    quote: str = Field(min_length=1, max_length=6000)


class ExtractedKnowledge(Strict):
    name: str = Field(min_length=1, max_length=200)
    fields: dict[str, GroundedField] = Field(default_factory=dict, max_length=24)
    unresolved: list[str] = Field(default_factory=list, max_length=20)
    followups: list[str] = Field(default_factory=list, max_length=5)
    mentions: list["EvaluationMention"] = Field(default_factory=list, max_length=12)
    claims: list[EvaluationClaim] = Field(default_factory=list, max_length=12)


class EvaluationMention(Strict):
    term: str = Field(min_length=1, max_length=200)
    answer_id: str = Field(min_length=1, max_length=36)
    quote: str = Field(min_length=1, max_length=1000)
    knowledge_ids: list[str] = Field(default_factory=list, max_length=4)
    explanation: str = Field(default="", max_length=1000)
    deferred: bool = False


class CollectionConfigInput(Strict):
    expected_revision: int = Field(ge=0)
    enabled: bool
    interval_hours: int = Field(ge=1, le=720)
    max_questions: int = Field(ge=1, le=10)


class RevisionInput(Strict):
    expected_revision: int = Field(ge=1)


class AnswerInput(RevisionInput):
    text: str = Field(min_length=1, max_length=6000)
    targets: list[QuestionTarget] = Field(default_factory=list, max_length=8)


class DeferInput(RevisionInput):
    days: int = Field(ge=0, le=90)


class EvaluationInput(Strict):
    # Compatibility for existing clients and historical calculation workflows.
    dataset_id: str | None = Field(default=None, min_length=1, max_length=80)
    project_id: int | None = Field(default=None, gt=0)
    text: str = Field(min_length=1, max_length=6000)

    @model_validator(mode="after")
    def scope_shape(self):
        if self.dataset_id and "project_id" in self.model_fields_set:
            raise ValueError("적용 범위와 기존 데이터셋 등록 방식을 함께 지정할 수 없습니다.")
        return self


class DraftUpdate(RevisionInput):
    proposal: CreateItem
    note: str = Field(min_length=1, max_length=2000)


class DraftReview(RevisionInput):
    decision: Literal["approve", "reject"]
    note: str = Field(min_length=1, max_length=2000)
    target_item_id: str | None = Field(default=None, max_length=36)
    target_revision: int | None = Field(default=None, ge=1)
